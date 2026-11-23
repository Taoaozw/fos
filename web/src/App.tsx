import { useEffect, useRef, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useQueryStates } from "nuqs"
import {
  IconAlertTriangle,
  IconBrain,
  IconLogout,
  IconMoon,
  IconPlus,
  IconRefresh,
  IconSun,
} from "@tabler/icons-react"
import { Toaster, toast } from "sonner"
import { AuditPanel } from "@/components/audit-panel"
import { AuthGate } from "@/components/auth-gate"
import { CardsPanel } from "@/components/cards-panel"
import { ConnectPanel } from "@/components/connect-panel"
import { GraphPanel } from "@/components/graph-panel"
import { OverviewPanel } from "@/components/overview-panel"
import { PlaygroundPanel } from "@/components/playground-panel"
import { ProposalsPanel } from "@/components/proposals-panel"
import { ScopeCombobox } from "@/components/shared/scope-combobox"
import { StatusPill } from "@/components/shared/status-pill"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Kbd } from "@/components/ui/kbd"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { ApiError, fosApi, getStoredToken, setStoredToken } from "@/lib/api"
import { DEFAULT_REMOTE_GIT_ROOT, resolveDefaultBrainUrl } from "@/lib/connect-templates"
import { spinOnce, usePanelTransition, useSlidingIndicator } from "@/lib/motion"
import { SCOPE_ALL } from "@/lib/scope-tree"
import {
  appNavParsers,
  cardsFilterParsers,
  type AppTab,
  type CardSpecialFilter,
} from "@/lib/url-state"

export function App() {
  const [token, setToken] = useState<string>(() => getStoredToken())
  const [authError, setAuthError] = useState<string>("")
  const [{ tab: activeTab, node: selectedNodeId }, setNav] = useQueryStates(appNavParsers)
  const [, setCardFilters] = useQueryStates(cardsFilterParsers)
  const [isDark, setIsDark] = useState<boolean>(false)
  const [themeVersion, setThemeVersion] = useState<number>(0)

  const [addOpen, setAddOpen] = useState(false)
  const [addKind, setAddKind] = useState("lesson")
  const [addSeverity, setAddSeverity] = useState("P1")
  const [addScope, setAddScope] = useState("global")
  const [addContent, setAddContent] = useState("")
  const [connectBrainUrl, setConnectBrainUrl] = useState<string>(() =>
    resolveDefaultBrainUrl(typeof window !== "undefined" ? window.location?.origin : undefined)
  )
  const [connectGitRoot, setConnectGitRoot] = useState<string>(DEFAULT_REMOTE_GIT_ROOT)
  const refreshIconRef = useRef<HTMLSpanElement | null>(null)
  const tabPanelRef = useRef<HTMLDivElement | null>(null)
  const tabsListRef = useRef<HTMLDivElement | null>(null)

  const queryClient = useQueryClient()
  usePanelTransition(tabPanelRef, [activeTab], { y: 5, duration: 0.28 })

  useEffect(() => {
    document.documentElement.classList.toggle("dark", isDark)
    setThemeVersion((v) => v + 1)
  }, [isDark])

  const healthQuery = useQuery({
    queryKey: ["fos", "health"],
    queryFn: () => fosApi.health(),
    retry: false,
  })

  const needsAuth = Boolean(healthQuery.data?.auth_enabled && !token)

  const dashboardQuery = useQuery({
    queryKey: ["fos", "dashboard", token],
    queryFn: () => fosApi.dashboard(80),
    enabled: Boolean(healthQuery.data) && !needsAuth,
    retry: false,
  })

  const tabIndicator = useSlidingIndicator(tabsListRef, dashboardQuery.data ? activeTab : "")

  useEffect(() => {
    if (dashboardQuery.error instanceof ApiError) {
      if (dashboardQuery.error.status === 401 || dashboardQuery.error.status === 403) {
        setAuthError("Bearer Token 无效，请重新输入")
        setStoredToken("")
        setToken("")
      }
    }
  }, [dashboardQuery.error])

  const addMutation = useMutation({
    mutationFn: () =>
      fosApi.addAtom({
        kind: addKind,
        scope: addScope.trim() || "global",
        content: addContent.trim(),
        severity: addSeverity,
      }),
    onSuccess: (res) => {
      toast.success(`已写入账本：${res.node?.id ?? res.atom.id}`)
      setAddOpen(false)
      setAddContent("")
      if (res.node?.id) void setNav({ node: res.node.id })
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const handleAddSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!addContent.trim()) return
    addMutation.mutate()
  }

  const handleJumpToCard = (nodeId: string) => {
    void setNav({ tab: "cards", node: nodeId }, { history: "push" })
  }

  const handleFilterCards = (opts: { scope?: string; special?: CardSpecialFilter }) => {
    void setCardFilters({
      scope: opts.scope ?? SCOPE_ALL,
      special: opts.special ?? "all",
      status: "active",
      severity: "__all__",
      kind: "__all__",
      q: "",
    })
    void setNav({ tab: "cards" }, { history: "push" })
  }

  const pendingProposals = dashboardQuery.data?.counts.pending_proposals ?? 0
  const snapVer = dashboardQuery.data?.snapshot_version ?? healthQuery.data?.snapshot_version ?? "-"

  return (
    <TooltipProvider delay={150}>
    <Tabs
      value={activeTab}
      onValueChange={(v) => void setNav({ tab: String(v) as AppTab }, { history: "push" })}
      className="flex min-h-screen flex-col gap-0 bg-background text-foreground"
    >
      <Toaster position="bottom-right" richColors />

      <header className="sticky top-0 z-20 border-b border-border/75 bg-card/88 backdrop-blur-md">
        <div className="flex h-13 w-full items-center gap-3 px-4 sm:px-5">
          {/* Left: Compact Brand + Status Tooltip */}
          <div className="flex shrink-0 items-center gap-2">
            <span className="icon-tile size-7 text-chart-2">
              <IconBrain className="size-4" />
            </span>
            <span className="hidden font-heading text-[15px] font-semibold tracking-tight text-foreground sm:inline">
              FlowOS
            </span>
            <Tooltip>
              <TooltipTrigger
                render={
                  <span className="inline-flex cursor-default">
                    <StatusPill
                      tone={dashboardQuery.data?.sqlite_vec ? "success" : "neutral"}
                      dot
                      pulse={Boolean(dashboardQuery.data?.sqlite_vec)}
                      mono
                      className="px-2 py-0 text-[11px]"
                    >
                      {snapVer}
                    </StatusPill>
                  </span>
                }
              />
              <TooltipContent side="bottom">
                snap {snapVer}
                {dashboardQuery.data
                  ? ` · vec:${dashboardQuery.data.sqlite_vec ? "on" : "off"} · ${dashboardQuery.data.embedder ?? "local_hash"}`
                  : ""}
              </TooltipContent>
            </Tooltip>
          </div>

          {/* Center: Single-Row Gliding Navigation Tabs */}
          {dashboardQuery.data ? (
            <div
              ref={tabsListRef}
              className="relative flex h-full min-w-0 flex-1 items-center overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            >
              <TabsList
                variant="line"
                className="h-full w-fit justify-start gap-0.5 p-0 text-[13px] [&_[data-slot=tabs-trigger]]:after:hidden"
              >
                <TabsTrigger
                  value="overview"
                  className="h-full flex-none rounded-none px-3 font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground data-active:text-foreground"
                >
                  总览
                </TabsTrigger>
                <TabsTrigger
                  value="proposals"
                  className="h-full flex-none rounded-none px-3 font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground data-active:text-foreground"
                >
                  推荐
                  <span
                    className={`ml-1 rounded-full px-1.5 py-0.5 font-mono text-[10px] leading-none font-medium ${
                      pendingProposals > 0
                        ? "bg-chart-3/16 text-chart-3"
                        : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {pendingProposals}
                  </span>
                </TabsTrigger>
                <TabsTrigger
                  value="cards"
                  className="h-full flex-none rounded-none px-3 font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground data-active:text-foreground"
                >
                  知识卡片
                  <span className="ml-1 rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px] leading-none text-muted-foreground">
                    {dashboardQuery.data.counts.active_nodes}
                  </span>
                </TabsTrigger>
                <TabsTrigger
                  value="graph"
                  className="h-full flex-none rounded-none px-3 font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground data-active:text-foreground"
                >
                  双时态图谱
                </TabsTrigger>
                <TabsTrigger
                  value="playground"
                  className="h-full flex-none rounded-none px-3 font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground data-active:text-foreground"
                >
                  检索 &amp; 照镜子
                </TabsTrigger>
                <TabsTrigger
                  value="audit"
                  className="h-full flex-none rounded-none px-3 font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground data-active:text-foreground"
                >
                  账本 &amp; 门禁
                  <span className="ml-1 rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px] leading-none text-muted-foreground">
                    {dashboardQuery.data.counts.ledger_atoms}
                  </span>
                </TabsTrigger>
                <TabsTrigger
                  value="connect"
                  className="h-full flex-none rounded-none px-3 font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground data-active:text-foreground"
                >
                  多端接入
                </TabsTrigger>
              </TabsList>
              {tabIndicator.ready && (
                <span
                  aria-hidden="true"
                  style={{
                    transform: `translate3d(${tabIndicator.left}px, 0, 0)`,
                    width: `${tabIndicator.width}px`,
                  }}
                  className="pointer-events-none absolute bottom-0 left-0 h-[2px] rounded-full bg-foreground transition-[transform,width] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
                />
              )}
            </div>
          ) : (
            <div className="flex-1" />
          )}

          {/* Right: Compact Actions */}
          <div className="flex shrink-0 items-center gap-1.5">
            {dashboardQuery.data && (
              <>
                <Button size="sm" className="h-8 rounded-lg shadow-2xs" onClick={() => setAddOpen(true)}>
                  <IconPlus className="size-3.5" />
                  <span className="hidden md:inline">新增记忆</span>
                  <span className="md:hidden">新增</span>
                </Button>
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="rounded-lg text-muted-foreground hover:text-foreground"
                        aria-label="刷新"
                        onClick={() => {
                          spinOnce(refreshIconRef.current)
                          void queryClient.invalidateQueries({ queryKey: ["fos"] })
                        }}
                      />
                    }
                  >
                    <span ref={refreshIconRef} className="inline-flex">
                      <IconRefresh className="size-4" />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">刷新数据</TooltipContent>
                </Tooltip>
              </>
            )}
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="rounded-lg text-muted-foreground hover:text-foreground"
                    aria-label="切换明暗主题"
                    onClick={() => setIsDark((d) => !d)}
                  />
                }
              >
                {isDark ? <IconSun className="size-4" /> : <IconMoon className="size-4" />}
              </TooltipTrigger>
              <TooltipContent side="bottom">{isDark ? "切换浅色模式" : "切换暗黑模式"}</TooltipContent>
            </Tooltip>
            {healthQuery.data?.auth_enabled && token && (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="rounded-lg text-muted-foreground hover:text-foreground"
                      aria-label="切换 Token"
                      onClick={() => {
                        setStoredToken("")
                        setToken("")
                        setAuthError("")
                      }}
                    />
                  }
                >
                  <IconLogout className="size-4" />
                </TooltipTrigger>
                <TooltipContent side="bottom">切换 Token</TooltipContent>
              </Tooltip>
            )}
          </div>
        </div>
      </header>

      <main className="w-full flex-1 px-4 py-3.5 sm:px-5">
        {healthQuery.isError ? (
          <Alert variant="destructive">
            <IconAlertTriangle className="size-4" />
            <AlertTitle>无法连接到 fosd 服务</AlertTitle>
            <AlertDescription>
              请确认 <Kbd>fosd serve</Kbd> 正在运行，或检查服务端端口与网络配置。
            </AlertDescription>
          </Alert>
        ) : needsAuth ? (
          <AuthGate
            errorMessage={authError}
            onSubmitToken={(tok) => {
              setAuthError("")
              setStoredToken(tok)
              setToken(tok)
            }}
          />
        ) : dashboardQuery.isLoading || !dashboardQuery.data ? (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-24 w-full rounded-2xl" />
              ))}
            </div>
            <Skeleton className="h-20 w-full rounded-2xl" />
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Skeleton className="h-80 w-full rounded-2xl" />
              <Skeleton className="h-80 w-full rounded-2xl" />
            </div>
          </div>
        ) : (
          <div ref={tabPanelRef}>
            <TabsContent value="overview">
              <OverviewPanel
                dashboard={dashboardQuery.data}
                onJumpToCard={handleJumpToCard}
                onFilterCards={handleFilterCards}
              />
            </TabsContent>

            <TabsContent value="proposals">
              <ProposalsPanel onJumpToCard={handleJumpToCard} />
            </TabsContent>

            <TabsContent value="cards">
              <CardsPanel
                dashboard={dashboardQuery.data}
                selectedNodeId={selectedNodeId}
                onSelectNode={(id) => void setNav({ node: id })}
              />
            </TabsContent>

            <TabsContent value="graph">
              <GraphPanel
                dashboard={dashboardQuery.data}
                onJumpToCard={handleJumpToCard}
                themeVersion={themeVersion}
              />
            </TabsContent>

            <TabsContent value="playground">
              <PlaygroundPanel
                dashboard={dashboardQuery.data}
                onJumpToCard={handleJumpToCard}
              />
            </TabsContent>

            <TabsContent value="audit">
              <AuditPanel dashboard={dashboardQuery.data} />
            </TabsContent>

            <TabsContent value="connect">
              <ConnectPanel
                health={healthQuery.data}
                token={token}
                brainUrl={connectBrainUrl}
                onBrainUrlChange={setConnectBrainUrl}
                remoteGitRoot={connectGitRoot}
                onRemoteGitRootChange={setConnectGitRoot}
              />
            </TabsContent>
          </div>
        )}
      </main>

      {/* Add Memory Dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              录入新记忆 <Kbd>fos add</Kbd>
            </DialogTitle>
            <DialogDescription>
              遵循「6 个月半衰期」标准：仅记录可复用的工程红线、个人原则或架构取舍（自动凭据脱敏）。
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleAddSubmit} className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Select value={addKind} onValueChange={(v) => setAddKind(v ?? "lesson")}>
                <SelectTrigger size="sm" className="w-32 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="lesson">lesson</SelectItem>
                  <SelectItem value="principle">principle</SelectItem>
                  <SelectItem value="decision">decision</SelectItem>
                  <SelectItem value="thought">thought</SelectItem>
                </SelectContent>
              </Select>
              <Select value={addSeverity} onValueChange={(v) => setAddSeverity(v ?? "P1")}>
                <SelectTrigger size="sm" className="w-24 font-mono text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="P0">P0</SelectItem>
                  <SelectItem value="P1">P1</SelectItem>
                  <SelectItem value="P2">P2</SelectItem>
                </SelectContent>
              </Select>
              <div className="min-w-44 flex-1">
                <ScopeCombobox
                  value={addScope}
                  onChange={setAddScope}
                  scopes={dashboardQuery.data?.scopes ?? []}
                />
              </div>
            </div>
            <Textarea
              rows={4}
              value={addContent}
              onChange={(e) => setAddContent(e.target.value)}
              placeholder="写下清晰可执行的规则与结构性原因..."
            />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setAddOpen(false)}>
                取消
              </Button>
              <Button type="submit" disabled={addMutation.isPending}>
                {addMutation.isPending ? "写入中..." : "写入不可变账本"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Tabs>
    </TooltipProvider>
  )
}

export default App
