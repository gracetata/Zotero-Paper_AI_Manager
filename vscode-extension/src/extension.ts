/**
 * Zotero Paper AI Manager — VS Code Extension
 *
 * 使用 GitHub Copilot (vscode.lm) 的 Claude 模型分析 Zotero 论文。
 * 无需额外 API key，Copilot 会员即可使用。
 *
 * 流程:
 *   监听 ~/Zotero/storage 新 PDF
 *   → python pdf_to_text.py KEY → 获取 PDF 文本
 *   → vscode.lm (Claude) 分析
 *   → python save_analysis.py KEY → 写回 Zotero
 */

import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';

// ── 全局状态 ──────────────────────────────────────────────────
let outputChannel: vscode.OutputChannel;
let fileWatcher: vscode.FileSystemWatcher | undefined;
let isWatching = false;
const recentlyProcessed = new Set<string>(); // 防止短时间内重复处理同一 key

// ── 工具函数 ──────────────────────────────────────────────────

function getConfig() {
    const cfg = vscode.workspace.getConfiguration('paperManager');
    const projectPath = cfg.get<string>('projectPath') || path.join(os.homedir(), 'Workspace', 'PaperManager');
    const zoteroStorage = cfg.get<string>('zoteroStoragePath') || path.join(os.homedir(), 'Zotero', 'storage');
    return {
        python: cfg.get<string>('pythonPath') || 'python3',
        project: projectPath,
        storage: zoteroStorage,
        model: cfg.get<string>('model') || 'claude-3.5-sonnet',
    };
}

function log(msg: string) {
    const ts = new Date().toLocaleTimeString('zh-CN');
    outputChannel.appendLine(`[${ts}] ${msg}`);
}

/** 从 Zotero storage 路径提取 Item Key（路径格式：.../storage/KEY/file.pdf）*/
function extractKeyFromPath(filePath: string): string | null {
    const parts = filePath.replace(/\\/g, '/').split('/');
    const storageIdx = parts.lastIndexOf('storage');
    if (storageIdx >= 0 && storageIdx + 1 < parts.length) {
        const key = parts[storageIdx + 1];
        if (/^[A-Z0-9]{8}$/.test(key)) {
            return key;
        }
    }
    return null;
}

/** 执行 Python 脚本，返回 stdout */
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
            if (code === 0) {
                resolve(stdout);
            } else {
                reject(new Error(`Python 退出码 ${code}\n${stderr.slice(-500)}`));
            }
        });
        if (stdinData !== undefined) {
            proc.stdin.write(stdinData);
            proc.stdin.end();
        }
    });
}

/** 加载 Read Paper Skill 系统提示 */
function loadSkillPrompt(projectPath: string): string {
    const skillFile = path.join(projectPath, 'skills', 'read-paper', 'SKILL.md');
    try {
        let content = fs.readFileSync(skillFile, 'utf-8');
        // 去掉 YAML frontmatter
        content = content.replace(/^---\n[\s\S]*?\n---\n/, '').trim();
        return content;
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

/** 加载配置文件中的标签白名单 */
function loadValidTags(projectPath: string): string[] {
    const configFile = path.join(projectPath, 'config.yaml');
    try {
        const content = fs.readFileSync(configFile, 'utf-8');
        // 简单解析 YAML tags 列表（不引入 yaml 依赖）
        const match = content.match(/^tags:\s*\n((?:\s+-\s*.+\n?)*)/m);
        if (match) {
            return match[1]
                .split('\n')
                .map(l => l.replace(/^\s+-\s*/, '').trim())
                .filter(Boolean);
        }
    } catch { /* ignore */ }
    return [
        '下肢假肢', '膝关节', '踝关节', '外骨骼',
        '移动机器人', '四足机器人', '人形机器人',
    ];
}

// ── 核心分析函数 ──────────────────────────────────────────────

async function analyzePaper(itemKey: string, autoTriggered = false) {
    if (recentlyProcessed.has(itemKey)) {
        log(`⏭️  跳过 ${itemKey}（刚处理过）`);
        return;
    }
    recentlyProcessed.add(itemKey);
    setTimeout(() => recentlyProcessed.delete(itemKey), 120_000); // 2 分钟内不重复

    const { project, model } = getConfig();
    outputChannel.show(true);
    log(`\n${'═'.repeat(60)}`);
    log(`🚀 开始分析: ${itemKey}`);

    // 自动触发时：先弹一个醒目通知，再进入进度条流程
    if (autoTriggered) {
        const picked = await vscode.window.showInformationMessage(
            `📄 检测到新论文 (${itemKey})，开始 AI 分析...`,
            { modal: false },
            '查看进度', '跳过此次'
        );
        if (picked === '跳过此次') {
            log(`⏭️  用户跳过分析: ${itemKey}`);
            recentlyProcessed.delete(itemKey);
            return;
        }
        if (picked === '查看进度') {
            outputChannel.show(true);
        }
    }

    await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: `📄 分析论文 ${itemKey}`,
        cancellable: false,
    }, async (progress) => {

        // ① 提取 PDF 文本
        progress.report({ message: '提取 PDF 文本...' });
        log('① 提取 PDF 文本...');
        let pdfText: string;
        try {
            pdfText = await runPython(['pdf_to_text.py', itemKey]);
            const lines = pdfText.trim().split('\n');
            const statusLine = lines[0];
            pdfText = lines.slice(1).join('\n');  // 第一行是状态信息
            log(`   ${statusLine}`);
        } catch (err) {
            log(`❌ PDF 提取失败: ${err}`);
            vscode.window.showErrorMessage(`PDF 提取失败: ${err}`);
            return;
        }

        if (!pdfText.trim()) {
            log('❌ PDF 文本为空，跳过分析');
            vscode.window.showWarningMessage(`${itemKey}: PDF 文本为空，可能尚未下载`);
            return;
        }

        // ② 选择 Copilot 模型
        progress.report({ message: `调用 ${model} 分析...` });
        log(`② 调用 Copilot 模型: ${model}`);

        const modelFamily = model.replace(/^claude-/, '').replace(/-\d{8}$/, '');
        let selectedModel: vscode.LanguageModelChat | undefined;

        // 按优先级尝试模型
        const familiesToTry = model.startsWith('claude') 
            ? [modelFamily, 'claude-3.5-sonnet', 'claude-3-sonnet', 'claude', 'gpt-4o']
            : ['gpt-4o', 'claude-3.5-sonnet'];

        for (const family of familiesToTry) {
            const models = await vscode.lm.selectChatModels({ vendor: 'copilot', family });
            if (models.length > 0) {
                selectedModel = models[0];
                log(`   使用模型: ${selectedModel.name} (family: ${family})`);
                break;
            }
        }

        if (!selectedModel) {
            log('❌ 未找到可用的 Copilot 模型，请确保 GitHub Copilot 已登录');
            vscode.window.showErrorMessage(
                '未找到可用的 Copilot 模型。请确保 GitHub Copilot 已登录并有效。'
            );
            return;
        }

        // ③ 构建 Prompt
        const skillPrompt = loadSkillPrompt(project);
        const validTags = loadValidTags(project);
        const tagInstruction = `

---
最后，从以下预定义标签中选择最相关的（严格只用这些标签，不要创建新标签）：
${validTags.join('、')}

在分析末尾单独一行输出：
TAGS: [标签1, 标签2, ...]
`;

        const maxChars = 60000; // vscode.lm 支持更长上下文
        const truncated = pdfText.length > maxChars;
        const usedText = truncated ? pdfText.slice(0, maxChars) : pdfText;
        const readPct = Math.round((usedText.length / pdfText.length) * 100);

        if (truncated) {
            log(`   ⚠️  文本已截断: 使用前 ${usedText.length} 字符 (${readPct}%)`);
        } else {
            log(`   ✅ 全文读取: ${usedText.length} 字符 (100%)`);
        }

        const messages = [
            vscode.LanguageModelChatMessage.Assistant(skillPrompt + tagInstruction),
            vscode.LanguageModelChatMessage.User(
                `请分析以下论文（${truncated ? `已截取前 ${readPct}%` : '全文'}）：\n\n${usedText}`
            ),
        ];

        // ④ 流式调用 LLM
        let analysis = '';
        try {
            const cts = new vscode.CancellationTokenSource();
            const response = await selectedModel.sendRequest(messages, {}, cts.token);
            let charCount = 0;
            for await (const chunk of response.text) {
                analysis += chunk;
                charCount += chunk.length;
                if (charCount % 500 < chunk.length) { // 每 500 字更新一次进度
                    progress.report({ message: `生成分析中... (${charCount} 字)` });
                }
            }
        } catch (err) {
            log(`❌ LLM 调用失败: ${err}`);
            vscode.window.showErrorMessage(`Claude 分析失败: ${err}`);
            return;
        }

        log(`   生成分析: ${analysis.length} 字符`);

        // ⑤ 写回 Zotero
        progress.report({ message: '写入 Zotero...' });
        log('③ 写入 Zotero（笔记 + 标签 + Markdown）...');
        try {
            const result = await runPython(['save_analysis.py', itemKey], analysis);
            log(result.trim());
        } catch (err) {
            log(`⚠️  写回 Zotero 失败: ${err}`);
            vscode.window.showWarningMessage(`分析已完成但写回 Zotero 失败: ${err}`);
            return;
        }

        log(`✅ 完成: ${itemKey}`);
        vscode.window.showInformationMessage(
            `✅ 论文分析完成: ${itemKey}`, '查看输出'
        ).then(choice => {
            if (choice === '查看输出') { outputChannel.show(); }
        });
    });
}

// ── 文件监听器 ────────────────────────────────────────────────

function startWatcher() {
    const { storage } = getConfig();
    if (fileWatcher) { fileWatcher.dispose(); }

    const pattern = new vscode.RelativePattern(
        vscode.Uri.file(storage),
        '**/*.pdf'
    );
    fileWatcher = vscode.workspace.createFileSystemWatcher(pattern);

    fileWatcher.onDidCreate(async (uri) => {
        const key = extractKeyFromPath(uri.fsPath);
        if (!key) { return; }
        log(`\n📡 新 PDF 检测到: ${uri.fsPath}`);
        log(`   Item Key: ${key}`);
        // 等待 5 秒让 Zotero 完成写入
        await new Promise(r => setTimeout(r, 5000));
        await analyzePaper(key, true /* autoTriggered */);
    });

    isWatching = true;
    log(`\n👁️  开始监听 Zotero storage: ${storage}`);
    vscode.window.setStatusBarMessage('$(eye) Paper AI: 监听中', 3000);
}

function stopWatcher() {
    if (fileWatcher) {
        fileWatcher.dispose();
        fileWatcher = undefined;
    }
    isWatching = false;
    log('⏹️  停止监听');
    vscode.window.setStatusBarMessage('$(eye-closed) Paper AI: 已停止', 3000);
}

// ── 扩展激活入口 ──────────────────────────────────────────────

export function activate(context: vscode.ExtensionContext) {
    outputChannel = vscode.window.createOutputChannel('Zotero Paper AI');
    log('🧠 Zotero Paper AI Manager 已激活（via GitHub Copilot）');

    // 状态栏按钮
    const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBar.text = '$(book) Paper AI';
    statusBar.tooltip = 'Zotero Paper AI Manager';
    statusBar.command = 'paperManager.toggleWatcher';
    statusBar.show();

    // 命令：按 Key 分析
    context.subscriptions.push(
        vscode.commands.registerCommand('paperManager.analyzeKey', async () => {
            const key = await vscode.window.showInputBox({
                prompt: '输入 Zotero Item Key（8位字母数字，如 LVSSLJLL）',
                placeHolder: 'LVSSLJLL',
                validateInput: v => /^[A-Z0-9]{8}$/i.test(v) ? null : '格式错误，应为8位字母数字',
            });
            if (key) { await analyzePaper(key.toUpperCase()); }
        })
    );

    // 命令：切换监听
    context.subscriptions.push(
        vscode.commands.registerCommand('paperManager.toggleWatcher', () => {
            if (isWatching) {
                stopWatcher();
                statusBar.text = '$(book) Paper AI';
            } else {
                startWatcher();
                statusBar.text = '$(eye) Paper AI: 监听中';
            }
        })
    );

    // 命令：分析所有未处理
    context.subscriptions.push(
        vscode.commands.registerCommand('paperManager.analyzeAll', async () => {
            const { python, project } = getConfig();
            outputChannel.show();
            log('\n📚 开始批量分析（--all 模式）...');
            const proc = cp.spawn(python, ['paper_analyzer.py', '--all'], {
                cwd: path.join(project, 'src'),
            });
            proc.stdout.on('data', d => log(d.toString().trim()));
            proc.stderr.on('data', d => log('⚠️ ' + d.toString().trim()));
            proc.on('close', code => log(`批量分析完成（退出码: ${code}）`));
        })
    );

    context.subscriptions.push(outputChannel, statusBar);

    // 启动时自动开始监听
    startWatcher();
}

export function deactivate() {
    stopWatcher();
}
