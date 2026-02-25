/**
 * Zotero Paper AI Manager — VS Code Extension
 *
 * 使用 GitHub Copilot (vscode.lm) 的 Claude 模型分析 Zotero 论文。
 * 无需额外 API key，Copilot 会员即可使用。
 *
 * 修复：改用 Node.js fs.watch (recursive) 代替 vscode.FileSystemWatcher，
 * 因为后者在工作区外路径上于 Linux 不可靠。
 */

import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';

let outputChannel: vscode.OutputChannel;
let fsWatcher: fs.FSWatcher | undefined;
let isWatching = false;
const recentlyProcessed = new Set<string>();
const pendingFiles = new Map<string, ReturnType<typeof setTimeout>>();

// 缓存一次查到的可用模型（避免每次分析都重新查询）
let cachedModels: vscode.LanguageModelChat[] = [];

async function getAvailableModels(): Promise<vscode.LanguageModelChat[]> {
    if (cachedModels.length > 0) { return cachedModels; }
    try {
        cachedModels = await vscode.lm.selectChatModels({ vendor: 'copilot' });
    } catch { cachedModels = []; }
    return cachedModels;
}

/** 根据用户设置的 model 字符串，在实际可用列表里找最匹配的模型 */
async function resolveModel(preferredFamily: string): Promise<vscode.LanguageModelChat | undefined> {
    const available = await getAvailableModels();
    if (available.length === 0) { return undefined; }

    // 1. 精确匹配 family
    let m = available.find(m => m.family === preferredFamily);
    if (m) { return m; }

    // 2. 归一化后匹配（忽略 - 和 . 的差异，如 "claude-sonnet-4-6" 匹配 "claude-sonnet-4.6"）
    const normalize = (s: string) => s.replace(/[-_.]/g, '').toLowerCase();
    m = available.find(m => normalize(m.family) === normalize(preferredFamily));
    if (m) { return m; }

    // 3. family 前缀匹配（"claude-sonnet" 匹配 "claude-sonnet-4.6"）
    const prefix = preferredFamily.split(/[-.]/).slice(0, 3).join('').toLowerCase();
    m = available.find(m => normalize(m.family).startsWith(prefix));
    if (m) { return m; }

    // 4. 只要是 claude 就行
    if (preferredFamily.startsWith('claude')) {
        m = available.find(m => m.family.includes('claude') || m.name.toLowerCase().includes('claude'));
        if (m) { return m; }
    }

    // 5. 返回列表第一个
    return available[0];
}

function getConfig() {
    const cfg = vscode.workspace.getConfiguration('paperManager');
    const projectPath = cfg.get<string>('projectPath') || path.join(os.homedir(), 'Workspace', 'PaperManager');
    const zoteroStorage = cfg.get<string>('zoteroStoragePath') || path.join(os.homedir(), 'Zotero', 'storage');
    return {
        python: cfg.get<string>('pythonPath') || 'python3',
        project: projectPath,
        storage: zoteroStorage,
        model: cfg.get<string>('model') || 'claude-sonnet-4.6',
    };
}

function log(msg: string) {
    const ts = new Date().toLocaleTimeString('zh-CN');
    outputChannel.appendLine(`[${ts}] ${msg}`);
}

function extractKeyFromPath(fullPath: string): string | null {
    const { storage } = getConfig();
    const normalized = fullPath.replace(/\\/g, '/');
    const storageNorm = storage.replace(/\\/g, '/').replace(/\/$/, '');
    let rel = normalized.startsWith(storageNorm)
        ? normalized.slice(storageNorm.length).replace(/^\//, '')
        : path.basename(path.dirname(fullPath));
    const key = rel.split('/')[0] || '';
    return /^[A-Z0-9]{8}$/i.test(key) ? key.toUpperCase() : null;
}

function runPython(args: string[], stdinData?: string): Promise<string> {
    return new Promise((resolve, reject) => {
        const { python, project } = getConfig();
        const proc = cp.spawn(python, args, {
            cwd: path.join(project, 'src'),
            env: { ...process.env },
        });
        let stdout = '';
        let stderr = '';
        proc.stdout.on('data', (d) => { stdout += d.toString(); });
        proc.stderr.on('data', (d) => { stderr += d.toString(); });
        proc.on('close', (code) => {
            code === 0 ? resolve(stdout) : reject(new Error(`退出码 ${code}\n${stderr.slice(-400)}`));
        });
        if (stdinData !== undefined) {
            proc.stdin.write(stdinData);
            proc.stdin.end();
        }
    });
}

function loadSkillPrompt(projectPath: string): string {
    try {
        let content = fs.readFileSync(path.join(projectPath, 'skills', 'read-paper', 'SKILL.md'), 'utf-8');
        return content.replace(/^---\n[\s\S]*?\n---\n/, '').trim();
    } catch {
        return `你是一个专业的学术论文分析助手。请仔细阅读论文全文，按以下结构进行分析：
1. 领域问题与挑战
2. 核心洞见（Insight）
3. 方法设计
4. 实验与指标
5. 启发与局限性
请使用中文，用 Markdown 格式输出，不要使用代码块包裹整个回复。`;
    }
}

function loadValidTags(projectPath: string): string[] {
    try {
        const content = fs.readFileSync(path.join(projectPath, 'config.yaml'), 'utf-8');
        const match = content.match(/^tags:\s*\n((?:\s+-\s*.+\n?)*)/m);
        if (match) {
            return match[1].split('\n').map(l => l.replace(/^\s+-\s*/, '').trim()).filter(Boolean);
        }
    } catch { /* ignore */ }
    return ['下肢假肢', '膝关节', '踝关节', '外骨骼', '移动机器人', '四足机器人', '人形机器人'];
}

function openChat(itemKey: string) {
    const { python, project } = getConfig();
    const scriptPath = path.join(project, 'src', 'paper_chat.py');
    const terminal = vscode.window.createTerminal({ name: `📄 Chat: ${itemKey}` });
    terminal.show(false);
    terminal.sendText(`${python} "${scriptPath}" --key ${itemKey} --no-pdf`);
}

async function analyzePaper(itemKey: string, autoTriggered = false) {
    if (recentlyProcessed.has(itemKey)) { return; }
    recentlyProcessed.add(itemKey);
    setTimeout(() => recentlyProcessed.delete(itemKey), 120_000);

    const { project, model } = getConfig();
    outputChannel.show(true);
    log(`\n${'═'.repeat(60)}`);
    log(`🚀 开始分析: ${itemKey}`);

    if (autoTriggered) {
        const picked = await vscode.window.showInformationMessage(
            `📄 检测到新论文 (${itemKey})，开始 AI 分析...`,
            { modal: false },
            '查看进度', '跳过此次'
        );
        if (picked === '跳过此次') { recentlyProcessed.delete(itemKey); return; }
        outputChannel.show(true);
    }

    await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: `📄 分析论文 ${itemKey}`,
        cancellable: false,
    }, async (progress) => {

        progress.report({ message: '提取 PDF 文本...' });
        log('① 提取 PDF 文本...');
        let pdfText: string;
        try {
            const raw = await runPython(['pdf_to_text.py', itemKey]);
            const lines = raw.trim().split('\n');
            log(`   ${lines[0]}`);
            pdfText = lines.slice(1).join('\n');
        } catch (err) {
            log(`❌ PDF 提取失败: ${err}`);
            vscode.window.showErrorMessage(`PDF 提取失败: ${err}`);
            return;
        }
        if (!pdfText.trim()) {
            log('❌ PDF 文本为空');
            vscode.window.showWarningMessage(`${itemKey}: PDF 文本为空，可能尚未下载`);
            return;
        }

        progress.report({ message: `调用 ${model} 分析...` });
        log(`② 调用 Copilot 模型 (偏好: ${model})...`);
        const selectedModel = await resolveModel(model);
        if (!selectedModel) {
            log('❌ 未找到 Copilot 模型，请确保已登录 GitHub Copilot');
            vscode.window.showErrorMessage('未找到可用的 Copilot 模型。请确保 GitHub Copilot 已登录。');
            return;
        }
        log(`   实际使用: ${selectedModel.name}  (family: ${selectedModel.family})`);

        const skillPrompt = loadSkillPrompt(project);
        const validTags = loadValidTags(project);
        const tagInstr = `\n\n---\n严格只从以下标签中选择（禁止创建新标签）：\n${validTags.join('、')}\n\n分析末尾单独一行输出：\nTAGS: [标签1, 标签2, ...]`;
        const maxChars = 60000;
        const totalChars = pdfText.length;
        let usedText: string;
        let readNote: string;

        if (totalChars <= maxChars) {
            usedText = pdfText;
            readNote = '全文';
            log(`   ✅ 全文读取: ${totalChars} 字符`);
        } else {
            // 首尾拼接：保留前 2/3 + 后 1/3，覆盖摘要/引言 和 实验/结论
            const headChars = Math.floor(maxChars * 0.67);
            const tailChars = maxChars - headChars;
            const head = pdfText.slice(0, headChars);
            const tail = pdfText.slice(-tailChars);
            usedText = head + '\n\n[... 中间部分已省略 ...]\n\n' + tail;
            const readPct = Math.round(maxChars / totalChars * 100);
            readNote = `首尾拼接 ${readPct}%（前 ${headChars} + 后 ${tailChars} 字符，共 ${totalChars}）`;
            log(`   ⚠️  文本超长，首尾拼接: 前${headChars} + 后${tailChars} 字符 (${readPct}% of ${totalChars})`);
        }

        const messages = [
            vscode.LanguageModelChatMessage.Assistant(skillPrompt + tagInstr),
            vscode.LanguageModelChatMessage.User(`请分析以下论文（${readNote}）：\n\n${usedText}`),
        ];

        let analysis = '';
        try {
            const cts = new vscode.CancellationTokenSource();
            const response = await selectedModel.sendRequest(messages, {}, cts.token);
            let n = 0;
            for await (const chunk of response.text) {
                analysis += chunk; n += chunk.length;
                if (n % 500 < chunk.length) { progress.report({ message: `生成分析中... (${n} 字)` }); }
            }
        } catch (err) {
            log(`❌ LLM 失败: ${err}`);
            vscode.window.showErrorMessage(`Claude 分析失败: ${err}`);
            return;
        }
        log(`   生成: ${analysis.length} 字符`);

        progress.report({ message: '写入 Zotero...' });
        log('③ 写入 Zotero...');
        try {
            log((await runPython(['save_analysis.py', itemKey], analysis)).trim());
        } catch (err) {
            log(`⚠️  写回失败: ${err}`);
            vscode.window.showWarningMessage(`分析完成但写回 Zotero 失败: ${err}`);
            return;
        }

        log(`✅ 完成: ${itemKey}`);
        const action = await vscode.window.showInformationMessage(
            `✅ 论文分析完成: ${itemKey}`, '追问对话', '查看日志'
        );
        if (action === '追问对话') { openChat(itemKey); }
        else if (action === '查看日志') { outputChannel.show(); }
    });
}

// ── 文件监听（Node.js fs.watch，比 vscode watcher 更可靠）────

function handleNewFile(fullPath: string) {
    if (!fullPath.endsWith('.pdf')) { return; }
    if (pendingFiles.has(fullPath)) { clearTimeout(pendingFiles.get(fullPath)!); }
    pendingFiles.set(fullPath, setTimeout(async () => {
        pendingFiles.delete(fullPath);
        if (!fs.existsSync(fullPath)) { return; }
        const key = extractKeyFromPath(fullPath);
        if (!key) { log(`⚠️  路径无法提取 Key: ${fullPath}`); return; }
        log(`\n📡 新 PDF: ${path.basename(fullPath)}  (Key: ${key})`);
        await new Promise(r => setTimeout(r, 5000)); // 等 Zotero 完成写入
        await analyzePaper(key, true);
    }, 3000)); // 3 秒去抖动
}

function startWatcher() {
    const { storage } = getConfig();
    if (fsWatcher) { try { fsWatcher.close(); } catch { /* ignore */ } }

    if (!fs.existsSync(storage)) {
        log(`❌ 路径不存在: ${storage}`);
        vscode.window.showErrorMessage(`Zotero storage 不存在: ${storage}\n请在设置中修改 paperManager.zoteroStoragePath`);
        return;
    }

    try {
        fsWatcher = fs.watch(storage, { recursive: true }, (event, filename) => {
            if (!filename) { return; }
            if (event === 'rename') {
                handleNewFile(path.join(storage, filename));
            }
        });
        fsWatcher.on('error', err => log(`⚠️  watcher 错误: ${err}`));
        isWatching = true;
        log(`\n👁️  监听启动: ${storage}`);
        log(`   监听类型: Node.js fs.watch (recursive)`);
    } catch (err) {
        log(`❌ 无法启动监听: ${err}`);
        vscode.window.showErrorMessage(`监听器启动失败: ${err}`);
    }
}

function stopWatcher() {
    if (fsWatcher) { try { fsWatcher.close(); } catch { /* ignore */ } fsWatcher = undefined; }
    isWatching = false;
    log('⏹️  监听已停止');
}

// ── 激活入口 ─────────────────────────────────────────────────

export function activate(context: vscode.ExtensionContext) {
    outputChannel = vscode.window.createOutputChannel('Zotero Paper AI');
    outputChannel.show(true);

    const { storage, project, python } = getConfig();
    log('🧠 Zotero Paper AI Manager 已激活（via GitHub Copilot）');
    log(`   项目: ${project}`);
    log(`   Zotero storage: ${storage}`);
    log(`   Python: ${python}`);

    const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBar.tooltip = '点击切换监听';
    statusBar.command = 'paperManager.toggleWatcher';
    statusBar.show();
    const updateBar = () => { statusBar.text = isWatching ? '$(eye) Paper AI: 监听中' : '$(book) Paper AI'; };

    context.subscriptions.push(
        vscode.commands.registerCommand('paperManager.analyzeKey', async () => {
            const key = await vscode.window.showInputBox({
                prompt: '输入 Zotero Item Key（8位字母数字）',
                placeHolder: 'LVSSLJLL',
                validateInput: v => /^[A-Z0-9]{8}$/i.test(v) ? null : '格式错误，应为8位字母数字',
            });
            if (key) { await analyzePaper(key.toUpperCase()); }
        }),
        vscode.commands.registerCommand('paperManager.toggleWatcher', () => {
            isWatching ? stopWatcher() : startWatcher();
            updateBar();
        }),
        vscode.commands.registerCommand('paperManager.analyzeAll', () => {
            outputChannel.show();
            log('\n📚 批量分析（--all）...');
            const proc = cp.spawn(python, ['paper_analyzer.py', '--all'], { cwd: path.join(project, 'src') });
            proc.stdout.on('data', d => log(d.toString().trim()));
            proc.stderr.on('data', d => log('⚠️ ' + d.toString().trim()));
            proc.on('close', code => log(`批量完成（退出码: ${code}）`));
        }),
        vscode.commands.registerCommand('paperManager.chatWithPaper', async () => {
            const key = await vscode.window.showInputBox({
                prompt: '输入要追问的论文 Zotero Item Key',
                placeHolder: 'LVSSLJLL',
                validateInput: v => /^[A-Z0-9]{8}$/i.test(v) ? null : '格式错误，应为8位字母数字',
            });
            if (key) { openChat(key.toUpperCase()); }
        }),
        vscode.commands.registerCommand('paperManager.debugStatus', async () => {
            outputChannel.show();
            log('\n🔧 调试信息:');
            log(`   监听状态: ${isWatching ? '✅ 运行中' : '❌ 已停止'}`);
            log(`   storage 存在: ${fs.existsSync(storage) ? '✅' : '❌ 路径不存在'}`);
            try { log(`   storage 目录数: ${fs.readdirSync(storage).length}`); } catch(e) { log(`   读取失败: ${e}`); }
            if (!isWatching) {
                const r = await vscode.window.showWarningMessage('监听器未运行，是否立即启动？', '启动');
                if (r === '启动') { startWatcher(); updateBar(); }
            } else {
                vscode.window.showInformationMessage('监听器运行正常 ✅');
            }
        }),
        vscode.commands.registerCommand('paperManager.listModels', async () => {
            outputChannel.show();
            log('\n🤖 查询 Copilot 可用模型...');
            cachedModels = []; // 强制刷新缓存
            try {
                const models = await vscode.lm.selectChatModels({ vendor: 'copilot' });
                cachedModels = models;
                if (models.length === 0) {
                    log('❌ 未找到任何模型，请确保 GitHub Copilot 已登录');
                    vscode.window.showErrorMessage('未找到 Copilot 模型，请先登录 GitHub Copilot');
                } else {
                    log(`✅ 共 ${models.length} 个可用模型：\n`);
                    for (const m of models) {
                        log(`   name:   ${m.name}`);
                        log(`   family: ${m.family}   ← 填入 paperManager.model 设置`);
                        log(`   id:     ${m.id}\n`);
                    }
                    const preferred = getConfig().model;
                    const resolved = await resolveModel(preferred);
                    log(`   当前设置: "${preferred}"`);
                    log(`   实际匹配: ${resolved ? `${resolved.name} (family: ${resolved.family})` : '❌ 无匹配'}`);
                }
            } catch(e) {
                log(`❌ 查询失败: ${e}`);
            }
        }),
        outputChannel, statusBar
    );

    startWatcher();
    updateBar();
}

export function deactivate() { stopWatcher(); }
