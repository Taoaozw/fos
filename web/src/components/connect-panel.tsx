import { useMemo, useRef, useState } from "react"
import {
  IconAlertTriangle,
  IconCheck,
  IconClockPlay,
  IconCloudDownload,
  IconCopy,
  IconDevices,
  IconEye,
  IconEyeOff,
  IconInfoCircle,
  IconPlugConnectedX,
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
  DEFAULT_REMOTE_GIT_ROOT,
  TOKEN_PLACEHOLDER,
  VITE_DEV_PORT,
  buildAgentSetupPrompt,
  buildAgentUnbindPrompt,
  buildAgentWorkflowPrompt,
  buildCurlExamples,
  buildFullUninstallCommands,
  buildInstallCommand,
  buildManualCommands,
  buildRemoteIngestCronCommand,
  buildRemoteIngestLaunchdScript,
  buildRemoteIngestManualCommands,
  buildUnbindOnlyCommands,
  isLoopbackUrl,
  isViteDevUrl,
  normalizeBrainUrl,
  resolveDefaultBrainUrl,
  validateBrainUrl,
} from "@/lib/connect-templates"
import { useRevealGroup } from "@/lib/motion"
import type { HealthResponse } from "@/lib/types"

interface ConnectPanelProps {
  health?: HealthResponse
  token: string
  brainUrl: string
  onBrainUrlChange: (value: string) => void
  remoteGitRoot: string
  onRemoteGitRootChange: (value: string) => void
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

export function ConnectPanel({
  health,
  token,
  brainUrl,
  onBrainUrlChange,
  remoteGitRoot,
  onRemoteGitRootChange,
}: ConnectPanelProps) {
  const defaultOrigin = useMemo(() => {
    return resolveDefaultBrainUrl(
      typeof window !== "undefined" ? window.location?.origin : undefined
    )
  }, [])

  const [includeRealToken, setIncludeRealToken] = useState<boolean>(false)
  const [promptTab, setPromptTab] = useState<string>("setup")
  const [methodTab, setMethodTab] = useState<string>("one-line")
  const [ingestTab, setIngestTab] = useState<string>("launchd")
  const [unbindTab, setUnbindTab] = useState<string>("unbind")
  const containerRef = useRef<HTMLDivElement | null>(null)

  useRevealGroup(containerRef, "[data-anim='connect-card']", [], {
    y: 6,
    duration: 0.24,
  })

  const urlError = validateBrainUrl(brainUrl)
  const effectiveUrl = normalizeBrainUrl(brainUrl)
  const effectiveToken = includeRealToken && token ? token : TOKEN_PLACEHOLDER
  const loopbackWarning = isLoopbackUrl(effectiveUrl)
  const viteDevWarning = isViteDevUrl(brainUrl)
  const cliPublished = Boolean(health?.cli_version)

  const handleBrainUrlChange = (next: string) => {
    if (includeRealToken && normalizeBrainUrl(next) !== effectiveUrl) {
      setIncludeRealToken(false)
    }
    onBrainUrlChange(next)
  }

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
    () => buildAgentSetupPrompt(effectiveUrl, effectiveToken, remoteGitRoot),
    [effectiveUrl, effectiveToken, remoteGitRoot]
  )
  const workflowPrompt = useMemo(() => buildAgentWorkflowPrompt(), [])
  const unbindPrompt = useMemo(() => buildAgentUnbindPrompt(), [])

  const ingestManualCmds = useMemo(
    () => buildRemoteIngestManualCommands(remoteGitRoot),
    [remoteGitRoot]
  )
  const ingestLaunchdScript = useMemo(
    () => buildRemoteIngestLaunchdScript(remoteGitRoot),
    [remoteGitRoot]
  )
  const ingestCronCmd = useMemo(
    () => buildRemoteIngestCronCommand(remoteGitRoot),
    [remoteGitRoot]
  )
  const unbindOnlyCmds = useMemo(() => buildUnbindOnlyCommands(), [])
  const fullUninstallCmds = useMemo(() => buildFullUninstallCommands(), [])

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
                在另一台电脑接入 / 管理 FlowOS
              </h2>
              <p className="text-xs text-muted-foreground">
                将另一台 Mac / Linux 的 CLI 与 AI Agent 绑定到常驻大脑，支持定时采集本机 Session/Git 上报与一键安全解绑
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

        {/* Interactive URL + Token + Remote Git Root controls */}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
          <div className="flex flex-col gap-1.5 lg:col-span-5">
            <label
              htmlFor="fos-brain-url"
              className="flex items-center justify-between text-xs font-medium text-foreground"
            >
              <span>大脑访问地址（Brain URL）</span>
            </label>
            <div className="flex items-center gap-2">
              <Input
                id="fos-brain-url"
                value={brainUrl}
                onChange={(e) => handleBrainUrlChange(e.target.value)}
                placeholder="http://100.x.y.z:7622"
                className="font-mono text-xs"
              />
              {brainUrl !== defaultOrigin && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0 rounded-lg text-xs"
                  onClick={() => handleBrainUrlChange(defaultOrigin)}
                >
                  <IconRefresh className="size-3.5" />
                  重置
                </Button>
              )}
            </div>
          </div>

          <div className="flex flex-col gap-1.5 lg:col-span-3">
            <label
              htmlFor="fos-remote-git-root"
              className="flex items-center justify-between text-xs font-medium text-foreground"
            >
              <span>远端代码目录（定时扫 Git 用）</span>
            </label>
            <Input
              id="fos-remote-git-root"
              value={remoteGitRoot}
              onChange={(e) => onRemoteGitRootChange(e.target.value)}
              placeholder="~/projects"
              className="font-mono text-xs"
            />
          </div>

          <div className="flex flex-col gap-1.5 lg:col-span-4">
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
                      隐藏
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
        {(urlError || loopbackWarning || viteDevWarning || !cliPublished) && (
          <div className="flex flex-col gap-2">
            {urlError && (
              <Alert variant="destructive" className="py-2.5">
                <IconAlertTriangle className="size-4" />
                <AlertTitle className="text-xs font-semibold">大脑地址格式无效</AlertTitle>
                <AlertDescription className="text-xs">{urlError}</AlertDescription>
              </Alert>
            )}
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
                    onClick={() => handleBrainUrlChange(resolveDefaultBrainUrl(brainUrl))}
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

      {/* 2. Agent Prompts (Setup + Daily Workflow + Unbind/Uninstall) */}
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
                覆盖「首次接入 + 配置定时采集」「日常协作规范」「安全解绑 / 卸载」三类完整指令；安装 Skill 后 Agent 也能从内置文档自动识别
              </p>
            </div>
          </div>
        </div>

        <Tabs value={promptTab} onValueChange={setPromptTab} className="flex flex-col gap-3">
          <TabsList className="w-fit flex-wrap">
            <TabsTrigger value="setup" className="text-xs">
              Prompt 1 · 首次接入 &amp; 定时采集
            </TabsTrigger>
            <TabsTrigger value="workflow" className="text-xs">
              Prompt 2 · 日常协作规范
            </TabsTrigger>
            <TabsTrigger value="unbind" className="text-xs">
              Prompt 3 · 解绑 / 卸载
            </TabsTrigger>
          </TabsList>

          <TabsContent value="setup">
            <div className="flex flex-col overflow-hidden rounded-xl border border-border/80 bg-card">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 bg-muted/35 px-3.5 py-2.5">
                <div className="flex items-center gap-2">
                  <IconSparkles className="size-4 text-chart-4" />
                  <div>
                    <div className="text-xs font-semibold text-foreground">
                      首次接入、安装 Skill 并配置本机 Session/Git 定时采集
                    </div>
                    <div className="text-[11px] text-muted-foreground">
                      直接发给另一台电脑的 Claude Code / Cursor / Codex / Antigravity，一站式完成安装与定时上报
                    </div>
                  </div>
                </div>
                <CopyButton
                  text={setupPrompt}
                  label="复制安装 Prompt"
                  successMessage="已复制「首次接入与定时采集 Prompt」"
                  variant="default"
                />
              </div>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words bg-muted/20 p-3.5 font-mono text-[12px] leading-relaxed text-foreground">
                {setupPrompt}
              </pre>
            </div>
          </TabsContent>

          <TabsContent value="workflow">
            <div className="flex flex-col overflow-hidden rounded-xl border border-border/80 bg-card">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 bg-muted/35 px-3.5 py-2.5">
                <div className="flex items-center gap-2">
                  <IconTerminal2 className="size-4 text-chart-2" />
                  <div>
                    <div className="text-xs font-semibold text-foreground">
                      日常协作规范（可选贴入全局 Rules / CLAUDE.md / AGENTS.md）
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
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words bg-muted/20 p-3.5 font-mono text-[12px] leading-relaxed text-foreground">
                {workflowPrompt}
              </pre>
            </div>
          </TabsContent>

          <TabsContent value="unbind">
            <div className="flex flex-col overflow-hidden rounded-xl border border-border/80 bg-card">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 bg-muted/35 px-3.5 py-2.5">
                <div className="flex items-center gap-2">
                  <IconPlugConnectedX className="size-4 text-chart-5" />
                  <div>
                    <div className="text-xs font-semibold text-foreground">
                      安全解绑大脑 / 完全卸载客户端
                    </div>
                    <div className="text-[11px] text-muted-foreground">
                      让那边 Agent 先冲刷离线 outbox、停用定时采集、再清除 Token/缓存或彻底卸载 CLI 与 Skill
                    </div>
                  </div>
                </div>
                <CopyButton
                  text={unbindPrompt}
                  label="复制解绑 Prompt"
                  successMessage="已复制「解绑 / 卸载 Prompt」"
                  variant="outline"
                />
              </div>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words bg-muted/20 p-3.5 font-mono text-[12px] leading-relaxed text-foreground">
                {unbindPrompt}
              </pre>
            </div>
          </TabsContent>
        </Tabs>
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
          <TabsList className="w-fit flex-wrap">
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

      {/* 4. Remote Device Scheduled Session / Git Ingest */}
      <section data-anim="connect-card" className="surface-card flex flex-col gap-4 p-5">
        <div className="flex items-center gap-3">
          <span className="icon-tile size-9 text-chart-3">
            <IconClockPlay className="size-4" />
          </span>
          <div>
            <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
              远端设备定时采集本机 Session / Git（可选）
            </h2>
            <p className="text-xs text-muted-foreground">
              一键安装默认不会在另一台电脑创建后台定时任务；若希望把那台电脑的 AI 会话与 Git 踩坑记录也自动汇总回大脑，按系统配置如下定时采集即可
            </p>
          </div>
        </div>

        <Tabs value={ingestTab} onValueChange={setIngestTab} className="flex flex-col gap-3">
          <TabsList className="w-fit flex-wrap">
            <TabsTrigger value="launchd" className="text-xs">
              macOS（launchd · 推荐）
            </TabsTrigger>
            <TabsTrigger value="cron" className="text-xs">
              Linux / 服务器（cron）
            </TabsTrigger>
            <TabsTrigger value="manual-ingest" className="text-xs">
              手动立即采集
            </TabsTrigger>
          </TabsList>

          <TabsContent value="launchd" className="flex flex-col gap-2.5">
            <p className="text-xs leading-relaxed text-muted-foreground">
              每 6 小时自动扫描本机 AI 会话（Claude / Codex / Antigravity / Pi 等）与 <Kbd>{remoteGitRoot || DEFAULT_REMOTE_GIT_ROOT}</Kbd> 下的 Git 提交，并增量上报到大脑；MacBook 合盖休眠唤醒后会自动补跑。<strong>无需</strong>在客户端运行 <Kbd>fos dream</Kbd>，M3 Max 大脑每晚会统一蒸馏编译。
            </p>
            <CodeBlock
              title="macOS launchd 一键配置脚本"
              code={ingestLaunchdScript}
              copyLabel="复制配置脚本"
            />
          </TabsContent>

          <TabsContent value="cron" className="flex flex-col gap-2.5">
            <p className="text-xs leading-relaxed text-muted-foreground">
              适用于 Linux 服务器或习惯使用 <Kbd>crontab</Kbd> 的环境，带 <Kbd># fos-client-ingest</Kbd> 标记以便后续一键清理。
            </p>
            <CodeBlock
              title="Linux crontab 一键配置命令"
              code={ingestCronCmd}
              copyLabel="复制 cron 命令"
            />
          </TabsContent>

          <TabsContent value="manual-ingest" className="flex flex-col gap-2.5">
            <p className="text-xs leading-relaxed text-muted-foreground">
              随时在另一台电脑手动触发一次增量采集并同步回大脑（内置游标去重，断网时自动写入本地 <Kbd>outbox.jsonl</Kbd>）。
            </p>
            <CodeBlock
              title="手动采集与同步命令"
              code={ingestManualCmds}
              copyLabel="复制采集命令"
            />
          </TabsContent>
        </Tabs>
      </section>

      {/* 5. Unbind & Full Uninstall */}
      <section data-anim="connect-card" className="surface-card flex flex-col gap-4 p-5">
        <div className="flex items-center gap-3">
          <span className="icon-tile size-9 text-chart-5">
            <IconPlugConnectedX className="size-4" />
          </span>
          <div>
            <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
              解绑远端大脑与完全卸载
            </h2>
            <p className="text-xs text-muted-foreground">
              切换设备、更换 Token 或不再需要在某台电脑使用 FlowOS 时，可按需选择「仅解绑」或「完全卸载」（仅清理当前客户端，不影响大脑主机数据）
            </p>
          </div>
        </div>

        <Tabs value={unbindTab} onValueChange={setUnbindTab} className="flex flex-col gap-3">
          <TabsList className="w-fit flex-wrap">
            <TabsTrigger value="unbind" className="text-xs">
              仅解绑远端大脑（保留 CLI）
            </TabsTrigger>
            <TabsTrigger value="uninstall" className="text-xs">
              完全卸载（CLI + Skill + 配置）
            </TabsTrigger>
          </TabsList>

          <TabsContent value="unbind" className="flex flex-col gap-2.5">
            <p className="text-xs leading-relaxed text-muted-foreground">
              先冲刷本地未上传的 <Kbd>outbox.jsonl</Kbd>，停用本机定时采集任务，并删除 <Kbd>~/.fos/config.json</Kbd>（含明文 Token）与离线缓存。
            </p>
            <CodeBlock
              title="仅解绑远端大脑"
              code={unbindOnlyCmds}
              copyLabel="复制解绑命令"
            />
          </TabsContent>

          <TabsContent value="uninstall" className="flex flex-col gap-2.5">
            <p className="text-xs leading-relaxed text-muted-foreground">
              在解绑与停用定时采集的基础上，同步清理各 AI 工具目录中的 <Kbd>fos</Kbd> Skill、卸载 <Kbd>fos</Kbd> CLI 并删除 <Kbd>~/.fos</Kbd> 目录。
            </p>
            <CodeBlock
              title="完全卸载 FlowOS 客户端"
              code={fullUninstallCmds}
              copyLabel="复制卸载命令"
            />
          </TabsContent>
        </Tabs>
      </section>

      {/* 6. Practical Tips & Troubleshooting */}
      <section data-anim="connect-card" className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="surface-card flex items-start gap-3 p-4">
          <span className="icon-tile size-8 shrink-0 text-chart-1">
            <IconWorld className="size-4" />
          </span>
          <div className="flex flex-col gap-1">
            <h3 className="text-xs font-semibold text-foreground">网络连通性（Tailscale / 局域网）</h3>
            <p className="text-xs leading-relaxed text-muted-foreground">
              主机须先运行 <Kbd>fosd init</Kbd> 开启 Bearer 鉴权，再以 <Kbd>fosd serve --host 0.0.0.0 --port 7622</Kbd> 监听。同一 Wi-Fi 下可用局域网 IP；外出或跨网络强烈建议通过{" "}
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
