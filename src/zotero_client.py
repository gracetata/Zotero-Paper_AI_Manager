"""
zotero_client.py — Zotero Web API 封装
读取：pyzotero（httpx，trust_env=False 绕过 socks 代理格式问题）
写入：requests 直接调用（pyzotero 自定义 client 时 API key 头部丢失）
"""

import os
import glob
import json
import yaml
import httpx
import requests
from pyzotero import zotero


def load_config(config_path=None):
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


class ZoteroClient:
    def __init__(self, config=None):
        if config is None:
            config = load_config()
        zot_cfg = config['zotero']
        self.library_id = zot_cfg['library_id']
        self.api_key = zot_cfg['api_key']
        self.library_type = zot_cfg['library_type']
        self.local_storage = zot_cfg['local_storage']

        # 读取客户端（pyzotero + httpx，trust_env=False 绕过 ALL_PROXY socks 格式问题）
        http_client = httpx.Client(trust_env=False)
        self.zot = zotero.Zotero(
            library_id=self.library_id,
            library_type=self.library_type,
            api_key=self.api_key,
            client=http_client
        )

        # 写入用的 requests session（API key 通过 header 传递，稳定可靠）
        self._write_session = requests.Session()
        self._write_session.headers.update({
            'Zotero-API-Key': self.api_key,
            'Zotero-API-Version': '3',
            'Content-Type': 'application/json',
        })
        self._base_url = f"https://api.zotero.org/users/{self.library_id}"

    def get_recent_items(self, limit=10):
        """获取最近添加的文献条目（按 dateAdded 倒序，排除笔记和附件）"""
        # Zotero top() 返回顶级条目（已自动排除附件和笔记）
        items = self.zot.top(limit=limit, sort='dateAdded', direction='desc')
        # 过滤掉纯笔记条目（保留论文类条目）
        skip_types = {'note', 'attachment'}
        return [it for it in items if it.get('data', {}).get('itemType') not in skip_types]

    def get_all_items(self):
        """
        分页获取 Zotero 库中全部顶级条目（排除笔记和附件）。
        自动处理分页，适合批量处理整个文献库。

        Returns:
            list: 所有条目列表（按 dateAdded 倒序）
        """
        skip_types = {'note', 'attachment'}
        all_items = []
        page_size = 100  # Zotero API 单页最大 100
        start = 0

        while True:
            batch = self.zot.top(limit=page_size, start=start,
                                 sort='dateAdded', direction='desc')
            if not batch:
                break
            filtered = [it for it in batch if it.get('data', {}).get('itemType') not in skip_types]
            all_items.extend(filtered)
            if len(batch) < page_size:
                break  # 已是最后一页
            start += page_size

        return all_items

    def get_item(self, item_key):
        """通过 key 获取单个条目"""
        return self.zot.item(item_key)

    def get_item_metadata(self, item):
        """从条目中提取常用元数据"""
        data = item.get('data', {})
        return {
            'key': data.get('key', ''),
            'title': data.get('title', '未知标题'),
            'authors': self._format_authors(data.get('creators', [])),
            'year': self._extract_year(data),
            'venue': data.get('publicationTitle') or data.get('conferenceName') or data.get('publisher') or '',
            'abstract': data.get('abstractNote', ''),
            'doi': data.get('DOI', ''),
            'url': data.get('url', ''),
            'date_added': data.get('dateAdded', ''),
            'existing_tags': [t['tag'] for t in data.get('tags', [])],
            'item_type': data.get('itemType', ''),
        }

    def find_local_pdf(self, item_key):
        """在本地 Zotero storage 中查找 PDF 文件"""
        pattern = os.path.join(self.local_storage, item_key, '*.pdf')
        pdfs = glob.glob(pattern)
        if pdfs:
            return pdfs[0]
        # 有时 PDF 附件是子条目，尝试从附件列表找
        return None

    def find_pdf_via_attachments(self, item_key):
        """通过 Zotero API 获取附件，找到本地 PDF 路径"""
        try:
            children = self.zot.children(item_key)
            for child in children:
                if child['data'].get('itemType') == 'attachment':
                    child_key = child['data']['key']
                    pdf_path = os.path.join(self.local_storage, child_key)
                    # 找到该目录下的 PDF
                    pattern = os.path.join(pdf_path, '*.pdf')
                    pdfs = glob.glob(pattern)
                    if pdfs:
                        return pdfs[0]
        except Exception as e:
            print(f"[WARN] 获取附件失败 ({item_key}): {e}")
        return None

    def add_note(self, item_key, note_content, note_title="📊 Copilot 论文分析"):
        """为条目添加 Zotero 笔记（用 requests 直接写入）"""
        html_content = self._markdown_to_html(note_content)
        note_data = [{
            'itemType': 'note',
            'parentItem': item_key,
            'note': f"<h1>{note_title}</h1>\n{html_content}",
            'tags': [],
            'collections': [],
            'relations': {},
        }]
        resp = self._write_session.post(f"{self._base_url}/items", json=note_data)
        if resp.status_code == 403:
            raise RuntimeError("Zotero API key 缺少写权限，请在 zotero.org/settings/keys 启用写访问")
        resp.raise_for_status()
        return resp.json()

    def add_tags(self, item_key, tags):
        """为条目添加标签（用 requests PATCH，不覆盖已有标签）"""
        # 先用 pyzotero 读取当前条目（含 version 字段，必须用于乐观锁）
        item = self.get_item(item_key)
        existing = [t['tag'] for t in item['data'].get('tags', [])]
        new_tags = [{'tag': t} for t in tags if t not in existing]
        if not new_tags:
            return True  # 标签已存在，无需更新

        all_tags = item['data'].get('tags', []) + new_tags
        version = item['data']['version']
        patch_data = {'tags': all_tags}
        headers = {'If-Unmodified-Since-Version': str(version)}
        resp = self._write_session.patch(
            f"{self._base_url}/items/{item_key}",
            json=patch_data,
            headers=headers
        )
        if resp.status_code == 403:
            raise RuntimeError("Zotero API key 缺少写权限，请在 zotero.org/settings/keys 启用写访问")
        if resp.status_code == 412:
            raise RuntimeError(f"Zotero 条目版本冲突（已在其他地方修改），请重试")
        resp.raise_for_status()
        return True

    def get_all_item_keys(self):
        """获取所有条目的 key 列表（用于 watchdog 比对）"""
        items = self.zot.top(limit=100, sort='dateAdded', direction='desc')
        return {item['data']['key'] for item in items}

    def _format_authors(self, creators):
        names = []
        for c in creators:
            if c.get('creatorType') == 'author':
                first = c.get('firstName', '')
                last = c.get('lastName', '')
                names.append(f"{last}, {first}".strip(', '))
        return '; '.join(names) if names else '未知作者'

    def _extract_year(self, data):
        date_str = data.get('date', '')
        if date_str:
            return date_str[:4]
        return ''

    def _markdown_to_html(self, md_text):
        """简单 Markdown 转 HTML（用于 Zotero 笔记）"""
        import re
        html = md_text
        # 标题
        html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
        # 粗体
        html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
        # 列表项
        html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        # 换行
        html = html.replace('\n\n', '<br><br>')
        return html
