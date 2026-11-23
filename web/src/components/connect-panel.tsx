import { useMemo, useRef, useState } from "react"
import {
  IconAlertTriangle,
  IconCheck,
  IconCloudDownload,
  IconCopy,
  IconDevices,
  IconEye,
  IconEyeOff,
  IconInfoCircle,
  IconRefresh,
  IconRobot,
  IconShieldLock,
  IconSparkles,
  IconTerminal2,
  IconWifi,
  IconWorld,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { StatusPill } from "@/components/shared/status-pill"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Kbd } from "@/components/ui/kbd"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  DEFAULT_BRAIN_PORT,
  TOKEN_PLACEHOLDER,
  VITE_DEV_PORT,
  buildAgentSetupPrompt,
  buildAgentWorkflowPrompt,
  buildCurlExamples,
  buildInstallCommand,
  buildManualCommands,
  isLoopbackUrl,
  isViteDevUrl,
  normalizeBrainUrl,
  resolveDefaultBrainUrl,
} from "@/lib/connect-templates"
import { useRevealGroup } from "@/lib/motion"
import type { HealthResponse } from "@/lib/types"

interface ConnectPanelProps {
  health?: HealthResponse
  token: string
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Fall through to legacy textarea copy (needed on plain HTTP LAN/Tailscale origins)
  }
  try {
    const ta = document.createElement("textarea")
    ta.value = text
    ta.setAttribute("readonly", "")
    ta.style.position = "fixed"
    ta.style.opacity = "0"
    ta.style.pointerEvents = "none"
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand("copy")
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

function CopyButton({
  text,
  label = "复制",
  successMessage = "已复制到剪贴板",
  variant = "outline",
}: {
  text: string
  label?: string
  successMessage?: string
  variant?: "outline" | "default" | "secondary" | "ghost"
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    const ok = await copyText(text)
    if (ok) {
      setCopied(true)
      toast.success(successMessage)
      window.setTimeout(() => setCopied(false), 1800)
    } else {
      toast.error("复制失败，请手动选中复制")
    }
  }

  return (
    <Button
      type="button"
      size="sm"
      variant={variant}
      className="h-7 shrink-0 gap-1.5 rounded-lg px-2.5 text-xs"
      onClick={() => void handleCopy()}
    >
      {copied ? (
        <>
          <IconCheck className="size-3.5 text-chart-2" />
          已复制
        </>
      ) : (
        <>
          <IconCopy className="size-3.5" />
          {label}
        </>
      )}
    </Button>
  )
}

function CodeBlock({
  title,
  code,
  copyLabel = "复制命令",
}: {
  title?: string
  code: string
  copyLabel?: string
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-border/80 bg-muted/45">
      <div className="flex items-center justify-between gap-2 border-b border-border/60 bg-muted/40 px-3 py-1.5">
        <span className="font-mono text-[11px] text-muted-foreground">{title ?? "Shell"}</span>
        <CopyButton text={code} label={copyLabel} />
      </div>
      <pre className="overflow-x-auto p-3 font-mono text-[12px] leading-relaxed text-foreground">
        <code>{code}</code>
      </pre>
    </div>
  )
}

const SUPPORTED_SKILL_TOOLS = [
  { tool: "Claude Code", dir: "~/.claude/skills/fos" },
  { tool: "Cursor", dir: "~/.cursor/skills/fos" },
  { tool: "Codex", dir: "~/.codex/skills/fos" },
  { tool: "Antigravity / Gemini", dir: "~/.gemini/config/skills/fos" },
  { tool: "Agents", dir: "~/.agents/skills/fos" },
  { tool: "Pi", dir: "~/.pi/agent/skills/fos" },
]

export function ConnectPanel({ health, token }: ConnectPanelProps) {
  const defaultOrigin = useMemo(() => {
    return resolveDefaultBrainUrl(
      typeof window !== "undefined" ? window.location?.origin : undefined
    )
  }, [])

  const [brainUrl, setBrainUrl] = useState<string>(defaultOrigin)
  const [includeRealToken, setIncludeRealToken] = useState<boolean>(false)
  const [methodTab, setMethodTab] = useState<string>("one-line")
  const containerRef = useRef<HTMLDivElement | null>(null)

  useRevealGroup(containerRef, "[data-anim='connect-card']", [], {
    y: 6,
    duration: 0.24,
  })

  const effectiveUrl = normalizeBrainUrl(brainUrl)
  const effectiveToken = includeRealToken && token ? token : TOKEN_PLACEHOLDER
  const loopbackWarning = isLoopbackUrl(effectiveUrl)
  const viteDevWarning = isViteDevUrl(effectiveUrl)
  const cliPublished = Boolean(health?.cli_version)

  const installCmd = useMemo(
    () => buildInstallCommand(effectiveUrl, effectiveToken),
    [effectiveUrl, effectiveToken]
  )
  const manualCmds = useMemo(
    () => buildManualCommands(effectiveUrl, effectiveToken, health?.cli_version),
    [effectiveUrl, effectiveToken, health?.cli_version]
  )
  const curlCmds = useMemo(
    () => buildCurlExamples(effectiveUrl, effectiveToken),
    [effectiveUrl, effectiveToken]
  )
  const setupPrompt = useMemo(
    () => buildAgentSetupPrompt(effectiveUrl, effectiveToken),
    [effectiveUrl, effectiveToken]
  )
  const workflowPrompt = useMemo(() => buildAgentWorkflowPrompt(), [])

  return (
    <div ref={containerRef} className="flex flex-col gap-4">
      {/* 1. Connection Config & Readiness */}
      <section data-anim="connect-card" className="surface-card flex flex-col gap-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="icon-tile size-9 text-chart-1">
              <IconDevices className="size-4" />
            </span>
            <div>
              <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
                在另一台电脑接入 FlowOS
              </h2>
              <p className="text-xs text-muted-foreground">
                将另一台 Mac / Linux 设备的 CLI 与 AI Agent 绑定到当前常驻大脑，自动同步离线缓存与 Skill
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <StatusPill tone={cliPublished ? "success" : "warning"} dot mono className="text-[11px]">
              {cliPublished ? `CLI Wheel v${health?.cli_version}` : "CLI Wheel 未发布"}
            </StatusPill>
            <StatusPill
              tone={health?.auth_enabled ? "info" : "neutral"}
              mono
              className="text-[11px]"
            >
              {health?.auth_enabled ? "Bearer 鉴权已开启" : "未开启鉴权"}
            </StatusPill>
            {health?.snapshot_version && (
              <StatusPill tone="neutral" mono className="text-[11px]">
                snap {health.snapshot_version}
              </StatusPill>
            )}
          </div>
        </div>

        {/* Interactive URL + Token controls */}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
          <div className="flex flex-col gap-1.5 lg:col-span-7">
            <label
              htmlFor="fos-brain-url"
              className="flex items-center justify-between text-xs font-medium text-foreground"
            >
              <span>大脑访问地址（Brain URL）</span>
              <span className="font-normal text-muted-foreground">
                下方所有安装命令与 Agent Prompt 会实时同步此地址
              </span>
            </label>
            <div className="flex items-center gap-2">
              <Input
                id="fos-brain-url"
                value={brainUrl}
                onChange={(e) => setBrainUrl(e.target.value)}
                placeholder="http://100.x.y.z:7622"
                className="font-mono text-xs"
              />
              {brainUrl !== defaultOrigin && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0 rounded-lg text-xs"
                  onClick={() => setBrainUrl(defaultOrigin)}
                >
                  <IconRefresh className="size-3.5" />
                  重置
                </Button>
              )}
            </div>
          </div>

          <div className="flex flex-col gap-1.5 lg:col-span-5">
            <span className="text-xs font-medium text-foreground">Token 填充方式</span>
            <div className="flex h-9 items-center justify-between gap-2 rounded-lg border border-border/80 bg-muted/35 px-3">
              <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                <IconShieldLock className="size-4 shrink-0 text-chart-2" />
                <span className="truncate font-mono">
                  {includeRealToken && token
                    ? `${token.slice(0, 8)}...${token.slice(-4)}`
                    : TOKEN_PLACEHOLDER}
                </span>
              </div>
              {token ? (
                <Button
                  type="button"
                  size="sm"
                  variant={includeRealToken ? "secondary" : "outline"}
                  className="h-7 shrink-0 gap-1 rounded-md px-2 text-[11px]"
                  onClick={() => setIncludeRealToken((v) => !v)}
                >
                  {includeRealToken ? (
                    <>
                      <IconEyeOff className="size-3.5" />
                      隐藏为占位符
                    </>
                  ) : (
                    <>
                      <IconEye className="size-3.5" />
                      填入当前 Token
                    </>
                  )}
                </Button>
              ) : (
                <Badge variant="outline" className="text-[10px]">
                  手动替换
                </Badge>
              )}
            </div>
          </div>
        </div>

        {/* Dynamic Readiness Alerts */}
        {(loopbackWarning || viteDevWarning || !cliPublished) && (
          <div className="flex flex-col gap-2">
            {viteDevWarning && (
              <Alert variant="destructive" className="py-2.5">
                <IconAlertTriangle className="size-4" />
                <AlertTitle className="flex flex-wrap items-center justify-between gap-2 text-xs font-semibold">
                  <span>端口 {VITE_DEV_PORT} 是前端开发服务器，不是 fosd 大脑端口</span>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-6 rounded-md px-2 text-[11px]"
                    onClick={() => setBrainUrl(resolveDefaultBrainUrl(effectiveUrl))}
                  >
                    改为 :{DEFAULT_BRAIN_PORT}
                  </Button>
                </AlertTitle>
                <AlertDescription className="text-xs">
                  访问 <Kbd>:{VITE_DEV_PORT}/install.sh</Kbd> 会返回前端 HTML 导致脚本语法报错；常驻大脑 <Kbd>fosd</Kbd> 监听在{" "}
                  <Kbd>:{DEFAULT_BRAIN_PORT}</Kbd> 端口。
                </AlertDescription>
              </Alert>
            )}
            {loopbackWarning && (
              <Alert className="border-chart-3/40 bg-chart-3/8 py-2.5">
                <IconAlertTriangle className="size-4 text-chart-3" />
                <AlertTitle className="text-xs font-semibold">
                  当前大脑地址是本机回环地址（{effectiveUrl}）
                </AlertTitle>
                <AlertDescription className="text-xs text-muted-foreground">
                  另一台电脑无法访问 <Kbd>localhost</Kbd> 或 <Kbd>127.0.0.1</Kbd>。请把上方地址改为这台主机的{" "}
                  <strong>Tailscale IP</strong>（如 <Kbd>http://100.x.y.z:7622</Kbd>）或同一局域网下的{" "}
                  <strong>LAN IP</strong>（如 <Kbd>http://192.168.x.x:7622</Kbd>），再复制下方命令或 Prompt。
                </AlertDescription>
              </Alert>
            )}
            {!cliPublished && (
              <Alert variant="destructive" className="py-2.5">
                <IconAlertTriangle className="size-4" />
                <AlertTitle className="text-xs font-semibold">大脑尚未发布 CLI 安装包</AlertTitle>
                <AlertDescription className="text-xs">
                  另一台电脑执行一键安装需要从大脑下载 wheel。请先在主机仓库根目录运行{" "}
                  <Kbd>fosd publish --project-dir .</Kbd> 后刷新本页。
                </AlertDescription>
              </Alert>
            )}
          </div>
        )}
      </section>

      {/* 2. Agent Prompts (Primary Action for Another Computer) */}
      <section data-anim="connect-card" className="surface-card flex flex-col gap-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="icon-tile size-9 text-chart-4">
              <IconRobot className="size-4" />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
                  给另一台电脑 AI Agent 用的 Prompt
                </h2>
                <Badge variant="secondary" className="text-[10px]">
                  可直接复制发送
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground">
                把「首次接入 Prompt」发给那边电脑的 Claude Code / Cursor / Codex / Antigravity，即可自动完成安装、绑定与验收
              </p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {/* Prompt A: Setup */}
          <div className="flex flex-col overflow-hidden rounded-xl border border-border/80 bg-card">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 bg-muted/35 px-3.5 py-2.5">
              <div className="flex items-center gap-2">
                <IconSparkles className="size-4 text-chart-4" />
                <div>
                  <div className="text-xs font-semibold text-foreground">
                    Prompt 1 · 首次接入与自动安装
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    发给另一台电脑的 Agent，一次性完成连通检查、安装 CLI、同步缓存与装 Skill
                  </div>
                </div>
              </div>
              <CopyButton
                text={setupPrompt}
                label="复制安装 Prompt"
                successMessage="已复制「首次接入安装 Prompt」"
                variant="default"
              />
            </div>
            <pre className="max-h-96 flex-1 overflow-auto whitespace-pre-wrap break-words bg-muted/20 p-3.5 font-mono text-[12px] leading-relaxed text-foreground">
              {setupPrompt}
            </pre>
          </div>

          {/* Prompt B: Daily Workflow */}
          <div className="flex flex-col overflow-hidden rounded-xl border border-border/80 bg-card">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 bg-muted/35 px-3.5 py-2.5">
              <div className="flex items-center gap-2">
                <IconTerminal2 className="size-4 text-chart-2" />
                <div>
                  <div className="text-xs font-semibold text-foreground">
                    Prompt 2 · 日常协作规范（可选贴入全局 Rules）
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    若那边 Agent 不支持自动加载 Skill，或想在会话开头显式约束用法时使用
                  </div>
                </div>
              </div>
              <CopyButton
                text={workflowPrompt}
                label="复制协作 Prompt"
                successMessage="已复制「日常协作规范 Prompt」"
                variant="outline"
              />
            </div>
            <pre className="max-h-96 flex-1 overflow-auto whitespace-pre-wrap break-words bg-muted/20 p-3.5 font-mono text-[12px] leading-relaxed text-foreground">
              {workflowPrompt}
            </pre>
          </div>
        </div>
      </section>

      {/* 3. Connection Methods */}
      <section data-anim="connect-card" className="surface-card flex flex-col gap-4 p-5">
        <div className="flex items-center gap-3">
          <span className="icon-tile size-9 text-chart-2">
            <IconCloudDownload className="size-4" />
          </span>
          <div>
            <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
              接入方式介绍
            </h2>
            <p className="text-xs text-muted-foreground">
              三种接入方式：推荐优先使用一键脚本；也可手动分步安装，或在无 CLI 环境下直接调用 HTTP API
            </p>
          </div>
        </div>

        <Tabs value={methodTab} onValueChange={setMethodTab} className="flex flex-col gap-3">
          <TabsList className="w-fit">
            <TabsTrigger value="one-line" className="text-xs">
              方式一：一键脚本（推荐）
            </TabsTrigger>
            <TabsTrigger value="manual" className="text-xs">
              方式二：手动分步安装
            </TabsTrigger>
            <TabsTrigger value="http" className="text-xs">
              方式三：HTTP API / 浏览器
            </TabsTrigger>
          </TabsList>

          <TabsContent value="one-line" className="flex flex-col gap-3">
            <p className="text-xs leading-relaxed text-muted-foreground">
              适用于 macOS / Linux。脚本会依次自动完成：检测并安装 <Kbd>uv</Kbd> → 从大脑下载经 SHA-256 校验的{" "}
              <Kbd>fos</Kbd> 客户端 wheel → 执行 <Kbd>fos init</Kbd> 绑定大脑与 Token → 执行 <Kbd>fos sync</Kbd>{" "}
              拉取离线快照缓存 → 执行 <Kbd>fos skill install</Kbd> 写入内嵌 AI Skill → 输出 <Kbd>fos status</Kbd> 体检结果。
            </p>
            <CodeBlock title="macOS / Linux 一键安装命令" code={installCmd} />
            <div className="rounded-xl border border-border/70 bg-muted/25 p-3">
              <div className="mb-2 text-xs font-medium text-foreground">
                自动识别并安装 Skill 的常见 AI 工具目录
              </div>
              <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                {SUPPORTED_SKILL_TOOLS.map((item) => (
                  <div
                    key={item.tool}
                    className="flex items-center justify-between gap-2 rounded-lg border border-border/60 bg-card/80 px-2.5 py-1.5 text-[11px]"
                  >
                    <span className="font-medium text-foreground">{item.tool}</span>
                    <span className="truncate font-mono text-muted-foreground">{item.dir}</span>
                  </div>
                ))}
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                若你的工具目录不在上述路径中，安装完成后可手动执行{" "}
                <Kbd>fos skill install --dir &lt;目标skills目录&gt;</Kbd>。
              </p>
            </div>
          </TabsContent>

          <TabsContent value="manual" className="flex flex-col gap-3">
            <p className="text-xs leading-relaxed text-muted-foreground">
              适合需要自定义 Python / <Kbd>uv</Kbd> 环境、公司内网代理、或想逐步排查网络与权限问题的场景。
            </p>
            <CodeBlock title="分步安装与验证命令" code={manualCmds} copyLabel="复制全部步骤" />
          </TabsContent>

          <TabsContent value="http" className="flex flex-col gap-3">
            <p className="text-xs leading-relaxed text-muted-foreground">
              无需安装本地 CLI：在另一台电脑的浏览器中直接打开{" "}
              <Kbd>{effectiveUrl}</Kbd> 即可使用本控制台；或在 CI / 轻量容器中直接携带{" "}
              <Kbd>Authorization: Bearer</Kbd> 请求 REST API。
            </p>
            <CodeBlock title="HTTP API (curl 示例)" code={curlCmds} copyLabel="复制 curl 示例" />
          </TabsContent>
        </Tabs>
      </section>

      {/* 4. Practical Tips & Troubleshooting */}
      <section data-anim="connect-card" className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="surface-card flex items-start gap-3 p-4">
          <span className="icon-tile size-8 shrink-0 text-chart-1">
            <IconWorld className="size-4" />
          </span>
          <div className="flex flex-col gap-1">
            <h3 className="text-xs font-semibold text-foreground">网络连通性（Tailscale / 局域网）</h3>
            <p className="text-xs leading-relaxed text-muted-foreground">
              主机需以 <Kbd>fosd serve --host 0.0.0.0 --port 7622</Kbd> 监听。同一 Wi-Fi 下可用局域网 IP；外出或跨网络强烈建议通过{" "}
              <strong>Tailscale</strong> 访问（地址固定且自带加密隧道），无需暴露公网端口。
            </p>
          </div>
        </div>

        <div className="surface-card flex items-start gap-3 p-4">
          <span className="icon-tile size-8 shrink-0 text-chart-2">
            <IconWifi className="size-4" />
          </span>
          <div className="flex flex-col gap-1">
            <h3 className="text-xs font-semibold text-foreground">断网离线降级与 Outbox 同步</h3>
            <p className="text-xs leading-relaxed text-muted-foreground">
              随身电脑断网或大脑暂时不可达时，<Kbd>fos boot</Kbd> / <Kbd>fos recall</Kbd> 会自动降级读取本地{" "}
              <Kbd>offline_cache</Kbd>，<Kbd>fos add</Kbd> 会写入本地 <Kbd>outbox.jsonl</Kbd>；恢复联网后运行{" "}
              <Kbd>fos sync</Kbd> 自动冲刷并拉取最新快照。
            </p>
          </div>
        </div>

        <div className="surface-card flex items-start gap-3 p-4">
          <span className="icon-tile size-8 shrink-0 text-chart-3">
            <IconInfoCircle className="size-4" />
          </span>
          <div className="flex flex-col gap-1">
            <h3 className="text-xs font-semibold text-foreground">CLI 自更新与 Skill 自动刷新</h3>
            <p className="text-xs leading-relaxed text-muted-foreground">
              当主机升级了 FlowOS 并重新运行 <Kbd>fosd publish --project-dir .</Kbd> 后，另一台电脑既会通过后台防抖自动更新，也可随时手动执行{" "}
              <Kbd>fos update</Kbd> 拉取最新 wheel（带 SHA-256 校验）并自动刷新所有已安装的 <Kbd>SKILL.md</Kbd>。
            </p>
          </div>
        </div>

        <div className="surface-card flex items-start gap-3 p-4">
          <span className="icon-tile size-8 shrink-0 text-chart-5">
            <IconShieldLock className="size-4" />
          </span>
          <div className="flex flex-col gap-1">
            <h3 className="text-xs font-semibold text-foreground">Token 与凭据安全提醒</h3>
            <p className="text-xs leading-relaxed text-muted-foreground">
              大脑只保存 Token 的 SHA-256 哈希，客户端明文 Token 保存在本机 <Kbd>~/.fos/config.json</Kbd>。给另一台电脑的 Agent 发送带真实 Token 的 Prompt 时，请确认会话非公开，且不要将 Token 写入任何仓库文件。
            </p>
          </div>
        </div>
      </section>
    </div>
  )
}
