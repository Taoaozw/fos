export const TOKEN_PLACEHOLDER = "<YOUR_TOKEN>"
export const FALLBACK_BRAIN_URL = "http://<m3max-tailscale-ip>:7622"
export const DEFAULT_BRAIN_PORT = "7622"
export const VITE_DEV_PORT = "5178"

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
export function buildManualCommands(brainUrl: string, token?: string): string {
  const url = normalizeBrainUrl(brainUrl)
  const tok = resolveToken(token)
  const tokArg = shellArg(tok)
  return [
    "# 1. 确保已安装 uv（若未安装请先运行: curl -LsSf https://astral.sh/uv/install.sh | sh）",
    `curl -fsSL -H "Authorization: Bearer ${tok}" "${url}/v1/cli/download" -o /tmp/fos-latest-py3-none-any.whl`,
    "uv tool install --force --reinstall /tmp/fos-latest-py3-none-any.whl",
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

/** Prompt A: Hand off to an AI Agent on another computer to install & verify fos. */
export function buildAgentSetupPrompt(brainUrl: string, token?: string): string {
  const url = normalizeBrainUrl(brainUrl)
  const tok = resolveToken(token)
  const installCmd = buildInstallCommand(url, tok)
  const hasPlaceholder = tok === TOKEN_PLACEHOLDER

  return [
    "请帮我在当前这台电脑上接入 FlowOS (`fos`) 个人认知记忆大脑，按以下步骤执行并验证：",
    "",
    "### 连接信息",
    `- Brain URL: \`${url}\``,
    `- Bearer Token: \`${tok}\`${hasPlaceholder ? "（如未填入请先向我索取）" : ""}`,
    "",
    "### 执行步骤",
    `1. **检查网络连通性**：先运行 \`curl -fsSL ${url}/v1/health\` 确认能访问大脑；若超时或连接被拒，请先提示我检查 Tailscale / 局域网或主机 \`fosd serve\` 状态，不要盲目重试。`,
    "2. **一键安装并绑定**：执行以下命令（会自动安装 `uv`、下载经 SHA-256 校验的 `fos` CLI、绑定大脑、拉取离线缓存并安装 AI Skill）：",
    "   ```bash",
    `   ${installCmd}`,
    "   ```",
    "   如果安装后提示找不到 `fos`，请将 `~/.local/bin` 加入当前 shell 的 `PATH`（并写入 `~/.zshrc` 或 `~/.bashrc`）。",
    "3. **确认 AI Skill 已安装**：运行 `fos status` 查看 `AI Skills` 列表；如果未自动识别到你当前使用的工具目录，请运行 `fos skill install --dir <你的skills目录>`。",
    "4. **验证读取**：运行 `fos boot` 确认能正常输出 L0 红线记忆。",
    "",
    "### 安全与汇报要求",
    "- **严禁**把 Bearer Token 写入任何 Git 仓库文件、提交记录或公开日志中（`fos init` 会安全写入本机 `~/.fos/config.json`）。",
    "- 完成后请向我汇报：`fos` 版本、大脑连通状态、离线缓存节点数、以及已安装的 Skill 路径。",
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
    "5. **离线与同步**：断网时 `fos` 会自动读本地缓存并将写入暂存到 outbox；恢复网络后运行 `fos sync` 同步，版本升级时运行 `fos update`。",
  ].join("\n")
}
