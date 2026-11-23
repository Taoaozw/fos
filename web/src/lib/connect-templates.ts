export const TOKEN_PLACEHOLDER = "<YOUR_TOKEN>"
export const FALLBACK_BRAIN_URL = "http://<m3max-tailscale-ip>:7622"
export const DEFAULT_BRAIN_PORT = "7622"
export const VITE_DEV_PORT = "5178"
export const DEFAULT_REMOTE_GIT_ROOT = "~/projects"
export const CLIENT_INGEST_LABEL = "com.clive.fos-client-ingest"
export const SESSION_INGEST_SOURCES = "antigravity claude codex pi fops"

function parseHttpUrl(raw: string): URL | null {
  const trimmed = raw.trim()
  if (!trimmed || trimmed === FALLBACK_BRAIN_URL) return null
  const withScheme = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(trimmed)
    ? trimmed
    : `http://${trimmed}`
  try {
    const parsed = new URL(withScheme)
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null
    if (!parsed.hostname) return null
    return parsed
  } catch {
    return null
  }
}

/** Validate user-entered Brain URL (HTTP/HTTPS origin only, no query/fragment/whitespace). */
export function validateBrainUrl(raw: string): string | null {
  const trimmed = raw.trim()
  if (!trimmed || trimmed === FALLBACK_BRAIN_URL) return null
  if (/\s/.test(trimmed)) return "大脑地址不能包含空白字符"
  const parsed = parseHttpUrl(trimmed)
  if (!parsed) return "请输入合法的 http:// 或 https:// 大脑地址（如 http://100.x.y.z:7622）"
  if (parsed.search || parsed.hash) {
    return "大脑基础地址不能包含查询参数（?）或锚点（#）"
  }
  return null
}

/** Normalize user-entered brain URL by trimming whitespace and trailing slashes. */
export function normalizeBrainUrl(raw: string): string {
  const trimmed = raw.trim().replace(/\/+$/, "")
  if (!trimmed) return FALLBACK_BRAIN_URL
  const parsed = parseHttpUrl(trimmed)
  if (parsed && !parsed.search && !parsed.hash) {
    const path = parsed.pathname.replace(/\/+$/, "")
    return `${parsed.origin}${path}`
  }
  return trimmed
}

/** Detect Vite dev server port (5178), which serves SPA HTML instead of fosd (7622). */
export function isViteDevUrl(raw: string): boolean {
  const parsed = parseHttpUrl(raw)
  if (parsed) return parsed.port === VITE_DEV_PORT
  return new RegExp(`:${VITE_DEV_PORT}(/|$)`).test(raw.trim())
}

/** Derive default Brain URL from browser origin or schemeless host:port, rewriting port 5178 -> 7622. */
export function resolveDefaultBrainUrl(origin?: string): string {
  const trimmed = (origin ?? "").trim().replace(/\/+$/, "")
  if (!trimmed) return FALLBACK_BRAIN_URL
  const parsed = parseHttpUrl(trimmed)
  if (!parsed) return FALLBACK_BRAIN_URL
  if (parsed.port === VITE_DEV_PORT) {
    parsed.port = DEFAULT_BRAIN_PORT
  }
  const path = parsed.pathname.replace(/\/+$/, "")
  return `${parsed.origin}${path}`
}

/** Detect loopback / non-routable hosts that another computer cannot reach. */
export function isLoopbackUrl(raw: string): boolean {
  const parsed = parseHttpUrl(raw)
  if (parsed) {
    const host = parsed.hostname.toLowerCase()
    return (
      host === "localhost" ||
      host === "127.0.0.1" ||
      host === "0.0.0.0" ||
      host === "::1" ||
      host === "[::1]"
    )
  }
  return /(^|\/\/)(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?(\/|$)/i.test(raw.trim())
}

function resolveToken(token?: string): string {
  const t = (token ?? "").trim()
  return t || TOKEN_PLACEHOLDER
}

function resolveGitRoot(gitRoot?: string): string {
  const r = (gitRoot ?? "").trim()
  return r || DEFAULT_REMOTE_GIT_ROOT
}

/** Strictly POSIX single-quote a value unless it contains only shell-safe literal characters. */
function shellArg(value: string): string {
  if (/^[A-Za-z0-9_./:@%+=,-]+$/.test(value)) return value
  return `'${value.replace(/'/g, "'\\''")}'`
}

/** Quote a path for interactive shell, expanding a leading ~/ via $HOME safely. */
function shellPathArg(rawPath: string): string {
  if (rawPath === "~") return '"$HOME"'
  if (rawPath.startsWith("~/")) {
    const rest = rawPath.slice(2)
    return `"$HOME"/${shellArg(rest)}`
  }
  return shellArg(rawPath)
}

/** Method 1: One-line installer served by fosd (/install.sh) with fail-fast temp file (no curl|sh 0-exit masking). */
export function buildInstallCommand(brainUrl: string, token?: string): string {
  const url = normalizeBrainUrl(brainUrl)
  const tok = resolveToken(token)
  const scriptUrl = shellArg(`${url}/install.sh`)
  return `tmp=$(mktemp) && curl -fsSL ${scriptUrl} -o "$tmp" && sh "$tmp" ${shellArg(tok)} && rm -f "$tmp"`
}

/** Method 2: Step-by-step manual setup with mktemp -d + fail-fast && chaining. */
export function buildManualCommands(
  brainUrl: string,
  token?: string,
  cliVersion?: string
): string {
  const url = normalizeBrainUrl(brainUrl)
  const tok = resolveToken(token)
  const ver = (cliVersion ?? "").trim() || "0.1.2"
  const wheelName = `fos-${ver}-py3-none-any.whl`
  const authHeader = shellArg(`Authorization: Bearer ${tok}`)
  const dlUrl = shellArg(`${url}/v1/cli/download`)
  return [
    `tmp_dir=$(mktemp -d) && curl -fsSL -H ${authHeader} ${dlUrl} -o "$tmp_dir/${wheelName}" && uv tool install --force --reinstall "$tmp_dir/${wheelName}" && rm -rf "$tmp_dir"`,
    `fos init --brain ${shellArg(url)} --token ${shellArg(tok)}`,
    "fos sync",
    "fos skill install",
    "fos status",
    "fos boot",
  ].join("\n")
}

/** Method 3: Pure HTTP API examples (single-quote safe for zsh/bash). */
export function buildCurlExamples(brainUrl: string, token?: string): string {
  const url = normalizeBrainUrl(brainUrl)
  const tok = resolveToken(token)
  const authHeader = shellArg(`Authorization: Bearer ${tok}`)
  return [
    `curl -fsSL ${shellArg(`${url}/v1/health`)}`,
    `curl -fsSL -H ${authHeader} ${shellArg(`${url}/v1/boot?budget=300`)}`,
    `curl -fsSL -H ${authHeader} ${shellArg(`${url}/v1/recall?q=deploy&budget=900`)}`,
  ].join("\n")
}

/** One-shot manual ingest commands for a remote device (session-only + explicit git root). */
export function buildRemoteIngestManualCommands(gitRoot?: string): string {
  const root = resolveGitRoot(gitRoot)
  return [
    `for src in ${SESSION_INGEST_SOURCES}; do fos ingest --from "$src" --since 48h --limit 25 --json; done`,
    `fos ingest --from git --root ${shellPathArg(root)} --since 48h --limit 30 --json`,
    "fos sync --json",
  ].join("\n")
}

function buildClientIngestScriptLines(gitRoot?: string): string[] {
  const root = resolveGitRoot(gitRoot)
  return [
    'FOS_HOME="${FOS_HOME:-$HOME/.fos}"',
    'mkdir -p "$FOS_HOME/bin" "$FOS_HOME/logs"',
    'cat <<"EOF" > "$FOS_HOME/bin/fos-client-ingest.sh"',
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"',
    `DEFAULT_GIT_ROOT=${shellArg(root)}`,
    'GIT_ROOT="${FOS_GIT_ROOT:-$DEFAULT_GIT_ROOT}"',
    'case "$GIT_ROOT" in "~") GIT_ROOT="$HOME" ;; "~/"*) GIT_ROOT="$HOME/${GIT_ROOT#~/}" ;; esac',
    'echo "=== [$(date -Iseconds)] FlowOS Client Ingest ==="',
    `for src in ${SESSION_INGEST_SOURCES}; do`,
    '  fos ingest --from "$src" --since 48h --limit 25 --json || true',
    "done",
    'if [ -d "$GIT_ROOT" ]; then',
    '  fos ingest --from git --root "$GIT_ROOT" --since 48h --limit 30 --json || true',
    "fi",
    "fos sync --json || true",
    "EOF",
    'chmod +x "$FOS_HOME/bin/fos-client-ingest.sh"',
  ]
}

/** macOS launchd setup script using plistlib for safe XML escaping + plutil -lint validation. */
export function buildRemoteIngestLaunchdScript(gitRoot?: string): string {
  return [
    ...buildClientIngestScriptLines(gitRoot),
    'mkdir -p "$HOME/Library/LaunchAgents"',
    `PLIST="$HOME/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist"`,
    `/usr/bin/python3 -c 'import os, pathlib, plistlib; home = os.environ["HOME"]; fos_home = os.environ.get("FOS_HOME") or os.path.join(home, ".fos"); p = pathlib.Path(home) / "Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist"; data = {"Label": "${CLIENT_INGEST_LABEL}", "ProgramArguments": [os.path.join(fos_home, "bin/fos-client-ingest.sh")], "StartInterval": 21600, "RunAtLoad": True, "StandardOutPath": os.path.join(fos_home, "logs/client-ingest.out.log"), "StandardErrorPath": os.path.join(fos_home, "logs/client-ingest.err.log")}; p.write_bytes(plistlib.dumps(data))'`,
    'plutil -lint "$PLIST"',
    'launchctl unload "$PLIST" 2>/dev/null || true',
    'launchctl load "$PLIST"',
    `launchctl list | grep ${CLIENT_INGEST_LABEL}`,
  ].join("\n")
}

/** Linux / POSIX cron setup script: installs standalone helper script so special chars never hit crontab. */
export function buildRemoteIngestCronCommand(gitRoot?: string): string {
  return [
    ...buildClientIngestScriptLines(gitRoot),
    'CRON_LINE="0 */6 * * * $FOS_HOME/bin/fos-client-ingest.sh >> $FOS_HOME/logs/client-ingest.out.log 2>&1 # fos-client-ingest"',
    'EXISTING="$(crontab -l 2>/dev/null)" || EXISTING=""',
    'printf "%s\\n%s\\n" "$(printf "%s\\n" "$EXISTING" | grep -vE \'# fos-client-ingest$\')" "$CRON_LINE" | sed \'/^$/d\' | crontab -',
    "crontab -l | grep -E '# fos-client-ingest$'",
  ].join("\n")
}

/** Unbind remote brain only: stop scheduler FIRST, verify outbox drained before deleting cache/config. */
export function buildUnbindOnlyCommands(): string {
  return [
    'FOS_HOME="${FOS_HOME:-$HOME/.fos}"',
    `launchctl unload "$HOME/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist" 2>/dev/null || true`,
    `rm -f "$HOME/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist" "$FOS_HOME/bin/fos-client-ingest.sh"`,
    'if EXISTING="$(crontab -l 2>/dev/null)"; then printf "%s\\n" "$EXISTING" | grep -vE \'# fos-client-ingest$\' | crontab -; fi',
    'SYNC_OUT="$(fos sync --json)" && printf "%s" "$SYNC_OUT" | grep -q \'"remaining_outbox":0\' || { echo "outbox 仍有未同步记录，已中止删除缓存；若确认放弃请手动执行 rm -rf \\"$FOS_HOME/config.json\\" \\"$FOS_HOME/cache\\""; exit 1; }',
    'rm -rf "$FOS_HOME/config.json" "$FOS_HOME/cache"',
    "fos status --offline",
  ].join("\n")
}

/** Full uninstall on a remote device (safe outbox check + stop scheduler + remove skills + uninstall CLI). */
export function buildFullUninstallCommands(): string {
  return [
    'FOS_HOME="${FOS_HOME:-$HOME/.fos}"',
    `launchctl unload "$HOME/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist" 2>/dev/null || true`,
    `rm -f "$HOME/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist" "$FOS_HOME/bin/fos-client-ingest.sh"`,
    'if EXISTING="$(crontab -l 2>/dev/null)"; then printf "%s\\n" "$EXISTING" | grep -vE \'# fos-client-ingest$\' | crontab -; fi',
    'SYNC_OUT="$(fos sync --json)" && printf "%s" "$SYNC_OUT" | grep -q \'"remaining_outbox":0\' || { echo "outbox 仍有未同步记录，已中止清理；若确认放弃请手动继续"; exit 1; }',
    'rm -rf "$HOME/.gemini/config/skills/fos" "$HOME/.claude/skills/fos" "$HOME/.codex/skills/fos" "$HOME/.agents/skills/fos" "$HOME/.cursor/skills/fos" "$HOME/.pi/agent/skills/fos"',
    "uv tool uninstall fos || true",
    'rm -rf "$FOS_HOME"',
  ].join("\n")
}

/** Prompt A: Hand off to an AI Agent on another computer to install, configure ingest & verify fos. */
export function buildAgentSetupPrompt(
  brainUrl: string,
  token?: string,
  gitRoot?: string
): string {
  const url = normalizeBrainUrl(brainUrl)
  const tok = resolveToken(token)
  const root = resolveGitRoot(gitRoot)
  const installCmd = buildInstallCommand(url, tok)
  const hasPlaceholder = tok === TOKEN_PLACEHOLDER

  return [
    "请帮我在当前这台电脑上接入 FlowOS (`fos`) 个人认知记忆大脑，按以下步骤执行并验证：",
    "",
    "### 连接信息",
    `- Brain URL: \`${url}\``,
    `- Bearer Token: \`${tok}\`${hasPlaceholder ? "（如未填入请先向我索取）" : ""}`,
    `- 本机代码根目录（用于定时扫描 Git 经验）: \`${root}\`（如实际目录不同请自动探测或向我确认）`,
    "",
    "### 执行步骤",
    `1. **检查网络连通性**：先运行 \`curl -fsSL ${shellArg(`${url}/v1/health`)}\` 确认能访问大脑；若超时或连接被拒，请先提示我检查 Tailscale / 局域网或主机 \`fosd serve\` 状态，不要盲目重试。`,
    "2. **一键安装并绑定**：执行以下命令（会自动安装 `uv`、下载经 SHA-256 校验的 `fos` CLI、绑定大脑、拉取离线缓存并安装 AI Skill）：",
    "   ```bash",
    `   ${installCmd}`,
    "   ```",
    "   如果安装后提示找不到 `fos`，请将 `~/.local/bin` 加入当前 shell 的 `PATH`（并写入 `~/.zshrc` 或 `~/.bashrc`）。",
    "3. **确认 AI Skill 已安装**：运行 `fos status` 查看 `AI Skills` 列表；如果未自动识别到你当前使用的工具目录，请运行 `fos skill install --dir <你的skills目录>`。",
    "4. **配置本机 Session / Git 定时采集上报（推荐）**：",
    `   - 先手动跑一次验证：仅扫描本机会话源（\`for src in ${SESSION_INGEST_SOURCES}; do fos ingest --from "$src" --since 48h --limit 25 --json; done\`）与指定目录 Git（\`fos ingest --from git --root ${shellPathArg(root)} --since 48h --limit 30 --json\`）+ \`fos sync --json\`（采集素材会暂存大脑账本，由大脑夜间 \`fos dream\` 统一过滤蒸馏，不会污染当前卡片）。`,
    `   - 再按本机系统配置每 6 小时自动采集上报：写入 \`~/.fos/bin/fos-client-ingest.sh\`，macOS 用 \`plistlib\` 生成并校验 \`~/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist\` 后 \`launchctl load\`；Linux 写入 \`crontab\`（行尾带 \`# fos-client-ingest\` 标记）。**不要**在本机跑 \`fos dream\` 或 \`--from docs\`。`,
    "5. **验证读取**：运行 `fos boot` 确认能正常输出 L0 红线记忆。",
    "",
    "### 安全与汇报要求",
    "- **严禁**把 Bearer Token 写入任何 Git 仓库文件、提交记录或公开日志中（`fos init` 会安全写入本机 `$FOS_HOME/config.json`）。",
    "- 完成后请向我汇报：`fos` 版本、大脑连通状态、离线缓存节点数、已安装的 Skill 路径、以及定时采集任务的加载状态。",
  ].join("\n")
}

/** Prompt B: Reusable workflow instructions for an Agent on another computer. */
export function buildAgentWorkflowPrompt(): string {
  return [
    "你在这台电脑上可以使用 `fos` (FlowOS) 作为我的跨设备个人认知记忆引擎（纯 CLI，已绑定远端大脑并支持断网离线缓存）。请遵守以下使用规范：",
    "",
    "1. **会话启动加载红线**：进入新项目或开始任务时，先运行 `fos boot` 加载与当前仓库/技术栈匹配的 L0 红线（<=300 tokens）。",
    "2. **重大决策前照镜子**：做架构改动、数据迁移、部署脚本或高风险变更前，先运行 `fos mirror \"<方案简述>\"` 检查历史冲突与踩坑预警。",
    "3. **按需渐进召回**：需要查过往经验/决策时运行 `fos recall \"<关键词>\"`，仅在需要原始证据时再 `fos get <ID> --tier L2 --graph --json`。",
    "4. **闭环沉淀与自纠错**：",
    "   - 遇到有复利价值的坑、原则或架构取舍：运行 `fos add --kind lesson|decision|principle --scope <scope> \"<规则>\"`（不要记录一次性琐事或敏感密钥）。",
    "   - 发现某条旧记忆与现状冲突或过时：主动运行 `fos correct <ID> \"<修正后规则>\" --reason \"<原因>\"` 或 `fos archive <ID> --reason \"<原因>\"`。",
    "   - 召回结果有帮助或属于噪音时，用 `fos feedback <ID> --useful|--noise` 反馈。",
    "5. **离线、采集与维护**：断网时 `fos` 会自动读本地缓存并将写入暂存到 outbox；恢复网络后运行 `fos sync` 同步，版本升级运行 `fos update`。",
  ].join("\n")
}

/** Prompt C: Hand off to an AI Agent on another computer to safely unbind or fully uninstall fos. */
export function buildAgentUnbindPrompt(): string {
  return [
    "请帮我在这台电脑上安全解绑（或完全卸载）FlowOS (`fos`) 客户端，请按以下顺序操作：",
    "",
    "### 先确认范围",
    "- 如果我没有特别说明，请先向我确认是要：**A) 仅解绑远端大脑**（停用定时采集并清除 Token/缓存，保留 `fos` CLI），还是 **B) 完全卸载**（连同 AI Skill、`fos` CLI 和 `$FOS_HOME` 一起清理）。",
    "- **严禁**误删远端大脑主机的数据（本操作仅清理当前这台随身电脑/服务器上的本地配置）。",
    "",
    "### 标准执行步骤",
    "1. **先停用本机定时采集任务**（防止清理期间新写入 outbox）：",
    `   - macOS：\`launchctl unload "$HOME/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist" 2>/dev/null || true\` 并删除该 plist 与 \`\${FOS_HOME:-$HOME/.fos}/bin/fos-client-ingest.sh\`。`,
    "   - Linux：仅在 `crontab -l` 成功时移除行尾匹配 `# fos-client-ingest$` 的条目。",
    "2. **安全冲刷离线队列**：运行 `fos sync --json` 并检查返回 `remaining_outbox == 0`；若同步失败或仍有未上传条目，**不要**直接删除缓存，先询问我是否确认放弃未同步的 `outbox.jsonl`。",
    "3. **按 CLI 实际路径清理绑定凭据与缓存**：解析 `FOS_HOME`（默认 `~/.fos`）以及 `config.json` 中自定义的 `cache_dir`（如有），删除 `config.json`（清除 Brain URL 与明文 Token）及缓存目录；同时检查当前 shell 环境变量里是否残留 `FOS_BRAIN_URL` / `FOS_TOKEN`。",
    "4. **若选择「完全卸载」则额外执行**：",
    "   - 删除各 AI 工具目录中的 `fos` Skill（`~/.gemini/config/skills/fos`、`~/.claude/skills/fos`、`~/.codex/skills/fos`、`~/.agents/skills/fos`、`~/.cursor/skills/fos`、`~/.pi/agent/skills/fos`）。",
    "   - 运行 `uv tool uninstall fos` 并删除 `$FOS_HOME` 目录。",
    "5. **验收汇报**：检查 `fos status --offline`（或 `which fos`）、环境变量及定时任务列表，向我确认 Token、定时任务和目标文件均已清理干净。",
  ].join("\n")
}
