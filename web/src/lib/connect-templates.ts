export const TOKEN_PLACEHOLDER = "<YOUR_TOKEN>"
export const FALLBACK_BRAIN_URL = "http://<m3max-tailscale-ip>:7622"
export const DEFAULT_BRAIN_PORT = "7622"
export const VITE_DEV_PORT = "5178"
export const DEFAULT_REMOTE_GIT_ROOT = "~/projects"
export const CLIENT_INGEST_LABEL = "com.clive.fos-client-ingest"

/** Normalize user-entered brain URL by trimming whitespace and trailing slashes. */
export function normalizeBrainUrl(raw: string): string {
  const trimmed = raw.trim().replace(/\/+$/, "")
  return trimmed || FALLBACK_BRAIN_URL
}

/** Detect Vite dev server port (5178), which serves SPA HTML instead of fosd (7622). */
export function isViteDevUrl(raw: string): boolean {
  const normalized = raw.trim()
  if (!normalized) return false
  try {
    const parsed = new URL(normalized.includes("://") ? normalized : `http://${normalized}`)
    return parsed.port === VITE_DEV_PORT
  } catch {
    return new RegExp(`:${VITE_DEV_PORT}(/|$)`).test(normalized)
  }
}

/** Derive default Brain URL from browser origin, rewriting Vite dev port 5178 -> 7622. */
export function resolveDefaultBrainUrl(origin?: string): string {
  const trimmed = (origin ?? "").trim().replace(/\/+$/, "")
  if (!trimmed) return FALLBACK_BRAIN_URL
  try {
    const parsed = new URL(trimmed)
    if (parsed.port === VITE_DEV_PORT) {
      parsed.port = DEFAULT_BRAIN_PORT
      return parsed.origin
    }
    return parsed.origin
  } catch {
    return trimmed
  }
}

/** Detect loopback / non-routable hosts that another computer cannot reach. */
export function isLoopbackUrl(raw: string): boolean {
  const normalized = raw.trim()
  if (!normalized) return false
  try {
    const parsed = new URL(normalized.includes("://") ? normalized : `http://${normalized}`)
    const host = parsed.hostname.toLowerCase()
    return (
      host === "localhost" ||
      host === "127.0.0.1" ||
      host === "0.0.0.0" ||
      host === "::1" ||
      host === "[::1]"
    )
  } catch {
    return /(^|\/\/)(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?(\/|$)/i.test(normalized)
  }
}

function resolveToken(token?: string): string {
  const t = (token ?? "").trim()
  return t || TOKEN_PLACEHOLDER
}

function resolveGitRoot(gitRoot?: string): string {
  const r = (gitRoot ?? "").trim()
  return r || DEFAULT_REMOTE_GIT_ROOT
}

/** Quote a value for POSIX shell only if it contains shell-sensitive characters. */
function shellArg(value: string): string {
  if (/^[A-Za-z0-9_./:@%+=,-]+$/.test(value)) return value
  return `'${value.replace(/'/g, "'\\''")}'`
}

/** Method 1: One-line installer served by fosd (/install.sh). */
export function buildInstallCommand(brainUrl: string, token?: string): string {
  const url = normalizeBrainUrl(brainUrl)
  const tok = resolveToken(token)
  return `curl -fsSL ${url}/install.sh | sh -s -- ${shellArg(tok)}`
}

/** Method 2: Step-by-step manual setup for custom environments or debugging. */
export function buildManualCommands(
  brainUrl: string,
  token?: string,
  cliVersion?: string
): string {
  const url = normalizeBrainUrl(brainUrl)
  const tok = resolveToken(token)
  const tokArg = shellArg(tok)
  const ver = (cliVersion ?? "").trim() || "0.1.0"
  const wheelPath = `/tmp/fos-${ver}-py3-none-any.whl`
  return [
    "# 1. 确保已安装 uv（若未安装请先运行: curl -LsSf https://astral.sh/uv/install.sh | sh）",
    `curl -fsSL -H "Authorization: Bearer ${tok}" "${url}/v1/cli/download" -o ${wheelPath}`,
    `uv tool install --force --reinstall ${wheelPath}`,
    "",
    "# 2. 绑定大脑地址与 Token，并拉取离线缓存",
    `fos init --brain ${shellArg(url)} --token ${tokArg}`,
    "fos sync",
    "",
    "# 3. 安装内嵌 AI Skill（自动识别 ~/.claude ~/.codex ~/.cursor ~/.gemini 等目录）",
    "fos skill install",
    "",
    "# 4. 验证连通性与 L0 红线加载",
    "fos status",
    "fos boot",
  ].join("\n")
}

/** Method 3: Pure HTTP API examples (no CLI required). */
export function buildCurlExamples(brainUrl: string, token?: string): string {
  const url = normalizeBrainUrl(brainUrl)
  const tok = resolveToken(token)
  return [
    `# 检查大脑健康状态`,
    `curl -fsSL "${url}/v1/health"`,
    "",
    `# 读取会话启动 L0 红线（<=300 tokens）`,
    `curl -fsSL -H "Authorization: Bearer ${tok}" "${url}/v1/boot?budget=300"`,
    "",
    `# 检索相关记忆`,
    `curl -fsSL -H "Authorization: Bearer ${tok}" "${url}/v1/recall?q=deploy&budget=900"`,
  ].join("\n")
}

/** One-shot manual ingest commands for a remote device. */
export function buildRemoteIngestManualCommands(gitRoot?: string): string {
  const root = resolveGitRoot(gitRoot)
  return [
    "# 扫描本机 AI 会话（claude / codex / antigravity / pi 等，自动增量游标去重）",
    "fos ingest --from all --since 48h --json",
    "",
    `# 扫描本机代码仓库中的 fix/revert/perf 提交与项目文档（按需修改目录）`,
    `fos ingest --from git --root ${root} --since 48h --limit 30 --json`,
    `fos ingest --from docs --root ${root} --limit 30 --json`,
    "",
    "# 冲刷离线 outbox 并拉取最新快照（无需在客户端跑 fos dream，M3 Max 大脑每晚统一编译）",
    "fos sync --json",
  ].join("\n")
}

/** macOS launchd setup script for scheduled remote device session/git ingest. */
export function buildRemoteIngestLaunchdScript(gitRoot?: string): string {
  const root = resolveGitRoot(gitRoot)
  return [
    "mkdir -p ~/.fos/bin ~/.fos/logs ~/Library/LaunchAgents",
    "",
    "cat <<'EOF' > ~/.fos/bin/fos-client-ingest.sh",
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"',
    `GIT_ROOT="\${FOS_GIT_ROOT:-${root.replace(/^~/, "$HOME")}}"`,
    'echo "=== [$(date -Iseconds)] FlowOS Client Ingest ==="',
    "fos ingest --from all --since 48h --limit 25 --json || true",
    'if [ -d "$GIT_ROOT" ]; then',
    '  fos ingest --from git --root "$GIT_ROOT" --since 48h --limit 30 --json || true',
    '  fos ingest --from docs --root "$GIT_ROOT" --limit 30 --json || true',
    "fi",
    "fos sync --json || true",
    "EOF",
    "chmod +x ~/.fos/bin/fos-client-ingest.sh",
    "",
    `cat <<EOF > ~/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist`,
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
    '<plist version="1.0"><dict>',
    `  <key>Label</key><string>${CLIENT_INGEST_LABEL}</string>`,
    "  <key>ProgramArguments</key><array><string>$HOME/.fos/bin/fos-client-ingest.sh</string></array>",
    "  <key>StartInterval</key><integer>21600</integer>",
    "  <key>RunAtLoad</key><true/>",
    "  <key>StandardOutPath</key><string>$HOME/.fos/logs/client-ingest.out.log</string>",
    "  <key>StandardErrorPath</key><string>$HOME/.fos/logs/client-ingest.err.log</string>",
    "</dict></plist>",
    "EOF",
    "",
    `launchctl unload ~/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist 2>/dev/null || true`,
    `launchctl load ~/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist`,
    `launchctl list | grep ${CLIENT_INGEST_LABEL}`,
  ].join("\n")
}

/** Linux / POSIX cron setup command for scheduled remote device session/git ingest. */
export function buildRemoteIngestCronCommand(gitRoot?: string): string {
  const root = resolveGitRoot(gitRoot)
  const resolvedRoot = root.replace(/^~/, "$HOME")
  const cronLine = `0 */6 * * * PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" fos ingest --from all --since 48h --limit 25 --json >> "$HOME/.fos/logs/client-ingest.out.log" 2>&1; [ -d "${resolvedRoot}" ] && PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" fos ingest --from git --root "${resolvedRoot}" --since 48h --limit 30 --json >> "$HOME/.fos/logs/client-ingest.out.log" 2>&1; PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" fos sync --json >> "$HOME/.fos/logs/client-ingest.out.log" 2>&1 # fos-client-ingest`
  return [
    "mkdir -p ~/.fos/logs",
    `(crontab -l 2>/dev/null | grep -v '# fos-client-ingest'; echo '${cronLine}') | crontab -`,
    "crontab -l | grep fos-client-ingest",
  ].join("\n")
}

/** Unbind remote brain only (keeps fos CLI installed, clears token/cache & stops scheduled ingest). */
export function buildUnbindOnlyCommands(): string {
  return [
    "# 1. 先冲刷本地未上传的离线 outbox（若大脑已不可达可跳过）",
    "fos sync --json || true",
    "",
    "# 2. 停用并移除本机定时采集任务（macOS launchd / Linux cron）",
    `launchctl unload ~/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist 2>/dev/null || true`,
    `rm -f ~/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist ~/.fos/bin/fos-client-ingest.sh`,
    "(crontab -l 2>/dev/null | grep -v '# fos-client-ingest' | crontab -) 2>/dev/null || true",
    "",
    "# 3. 清除远端大脑绑定配置（含明文 Token）与离线缓存",
    "rm -f ~/.fos/config.json",
    "rm -rf ~/.fos/cache",
    "",
    "# 4. 验证已解绑（应不再显示 brain_url）",
    "fos status --offline",
  ].join("\n")
}

/** Full uninstall on a remote device (unbind + remove AI skills + uninstall fos CLI + clean ~/.fos). */
export function buildFullUninstallCommands(): string {
  return [
    "# 1. 先冲刷本地未上传的离线 outbox（若大脑已不可达可跳过）",
    "fos sync --json || true",
    "",
    "# 2. 停用并移除本机定时采集任务（macOS launchd / Linux cron）",
    `launchctl unload ~/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist 2>/dev/null || true`,
    `rm -f ~/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist ~/.fos/bin/fos-client-ingest.sh`,
    "(crontab -l 2>/dev/null | grep -v '# fos-client-ingest' | crontab -) 2>/dev/null || true",
    "",
    "# 3. 移除已安装到各 AI 工具目录的 fos Skill",
    "rm -rf ~/.gemini/config/skills/fos ~/.claude/skills/fos ~/.codex/skills/fos ~/.agents/skills/fos ~/.cursor/skills/fos ~/.pi/agent/skills/fos",
    "",
    "# 4. 卸载 fos CLI 并清理本地配置与缓存",
    "uv tool uninstall fos || true",
    "rm -rf ~/.fos",
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
    `1. **检查网络连通性**：先运行 \`curl -fsSL ${url}/v1/health\` 确认能访问大脑；若超时或连接被拒，请先提示我检查 Tailscale / 局域网或主机 \`fosd serve\` 状态，不要盲目重试。`,
    "2. **一键安装并绑定**：执行以下命令（会自动安装 `uv`、下载经 SHA-256 校验的 `fos` CLI、绑定大脑、拉取离线缓存并安装 AI Skill）：",
    "   ```bash",
    `   ${installCmd}`,
    "   ```",
    "   如果安装后提示找不到 `fos`，请将 `~/.local/bin` 加入当前 shell 的 `PATH`（并写入 `~/.zshrc` 或 `~/.bashrc`）。",
    "3. **确认 AI Skill 已安装**：运行 `fos status` 查看 `AI Skills` 列表；如果未自动识别到你当前使用的工具目录，请运行 `fos skill install --dir <你的skills目录>`。",
    "4. **配置本机 Session / Git 定时采集上报（推荐）**：",
    `   - 先手动跑一次验证：\`fos ingest --from all --since 48h --json\`（如有代码目录再跑 \`fos ingest --from git --root ${root} --since 48h --limit 30 --json\`）+ \`fos sync --json\`。`,
    `   - 再按本机系统配置每 6 小时自动采集上报：macOS 创建 \`~/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist\`（调用 \`~/.fos/bin/fos-client-ingest.sh\` 并 \`launchctl load\`）；Linux 写入 \`crontab\`（带 \`# fos-client-ingest\` 注释标记）。**不要**在本机跑 \`fos dream\`，大脑主机每晚会统一编译。`,
    "5. **验证读取**：运行 `fos boot` 确认能正常输出 L0 红线记忆。",
    "",
    "### 安全与汇报要求",
    "- **严禁**把 Bearer Token 写入任何 Git 仓库文件、提交记录或公开日志中（`fos init` 会安全写入本机 `~/.fos/config.json`）。",
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
    "5. **离线、采集与维护**：断网时 `fos` 会自动读本地缓存并将写入暂存到 outbox；恢复网络后运行 `fos sync` 同步，版本升级运行 `fos update`；需要上报本机 session 时跑 `fos ingest --from all --since 48h && fos sync`。",
  ].join("\n")
}

/** Prompt C: Hand off to an AI Agent on another computer to safely unbind or fully uninstall fos. */
export function buildAgentUnbindPrompt(): string {
  return [
    "请帮我在这台电脑上安全解绑（或完全卸载）FlowOS (`fos`) 客户端，请按以下顺序操作：",
    "",
    "### 先确认范围",
    "- 如果我没有特别说明，请先向我确认是要：**A) 仅解绑远端大脑**（清除 Token/缓存并停用定时采集，保留 `fos` CLI），还是 **B) 完全卸载**（连同 AI Skill、`fos` CLI 和 `~/.fos` 一起清理）。",
    "- **严禁**误删远端大脑主机的数据（本操作仅清理当前这台随身电脑/服务器上的本地配置）。",
    "",
    "### 标准执行步骤",
    "1. **先冲刷离线缓存**：尝试执行 `fos sync --json` 确保本地 `outbox.jsonl` 中尚未上传的记忆先同步回大脑（若大脑已不可达或我要求直接放弃未同步队列，可跳过）。",
    "2. **停用并删除本机定时采集任务**：",
    `   - macOS：\`launchctl unload ~/Library/LaunchAgents/${CLIENT_INGEST_LABEL}.plist 2>/dev/null || true\` 并删除该 plist 与 \`~/.fos/bin/fos-client-ingest.sh\`。`,
    "   - Linux：从 `crontab` 中移除带 `# fos-client-ingest` 标记的定时行。",
    "3. **解绑远端凭据与缓存**：删除 `~/.fos/config.json`（清除 Brain URL 与明文 Bearer Token）及 `~/.fos/cache/`。",
    "4. **若选择「完全卸载」则额外执行**：",
    "   - 删除各 AI 工具目录中的 `fos` Skill（`~/.gemini/config/skills/fos`、`~/.claude/skills/fos`、`~/.codex/skills/fos`、`~/.agents/skills/fos`、`~/.cursor/skills/fos`、`~/.pi/agent/skills/fos`）。",
    "   - 运行 `uv tool uninstall fos` 并删除 `~/.fos` 目录。",
    "5. **验收汇报**：检查 `fos status --offline`（或 `which fos`）及定时任务列表，向我确认 Token、定时任务和目标文件均已清理干净。",
  ].join("\n")
}
