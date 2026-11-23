import { useEffect, useRef, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
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
import { GraphPanel } from "@/components/graph-panel"
import { OverviewPanel } from "@/components/overview-panel"
import { PlaygroundPanel } from "@/components/playground-panel"
import { ProposalsPanel } from "@/components/proposals-panel"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
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
import { spinOnce, usePanelTransition } from "@/lib/motion"
import type { CardFilterPreset } from "@/lib/types"

export function App() {
  const [token, setToken] = useState<string>(() => getStoredToken())
  const [authError, setAuthError] = useState<string>("")
  const [activeTab, setActiveTab] = useState<string>("overview")
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [cardFilterPreset, setCardFilterPreset] = useState<CardFilterPreset | null>(null)
  const [isDark, setIsDark] = useState<boolean>(false)
  const [themeVersion, setThemeVersion] = useState<number>(0)

  const [addOpen, setAddOpen] = useState(false)
  const [addKind, setAddKind] = useState("lesson")
  const [addSeverity, setAddSeverity] = useState("P1")
  const [addScope, setAddScope] = useState("global")
  const [addContent, setAddContent] = useState("")
  const refreshIconRef = useRef<HTMLSpanElement | null>(null)
  const tabPanelRef = useRef<HTMLDivElement | null>(null)

  const queryClient = useQueryClient()
  usePanelTransition(tabPanelRef, [activeTab], { y: 6, duration: 0.22 })

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
      if (res.node?.id) setSelectedNodeId(res.node.id)
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
    setSelectedNodeId(nodeId)
    setActiveTab("cards")
  }

  const handleFilterCards = (opts: { scope?: string; special?: "all" | "patterns" | "issues" | "guards" }) => {
    setCardFilterPreset({ ...opts, nonce: Date.now() })
    setActiveTab("cards")
  }

  return (
    <TooltipProvider delay={150}>
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <Toaster position="bottom-right" richColors />

      <header className="sticky top-0 z-20 border-b border-border bg-background/95 backdrop-blur">
        <div className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-2">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <IconBrain className="size-4 text-chart-2" />
              <span className="font-mono text-sm font-semibold tracking-tight">FlowOS</span>
            </div>
            <span className="text-border">/</span>
            <Badge variant="outline" className="rounded-sm border-border/80 bg-card font-mono text-[11px] text-muted-foreground">
              snap {dashboardQuery.data?.snapshot_version ?? healthQuery.data?.snapshot_version ?? "-"}
            </Badge>
            {dashboardQuery.data && (
              <Badge variant="outline" className="rounded-sm border-border/80 bg-card font-mono text-[11px] text-muted-foreground">
                <span className={`mr-1.5 inline-block size-1.5 rounded-full ${dashboardQuery.data.sqlite_vec ? "bg-chart-2" : "bg-muted-foreground"}`} />
                vec:{dashboardQuery.data.sqlite_vec ? "on" : "off"} · {dashboardQuery.data.embedder ?? "local_hash"}
              </Badge>
            )}
          </div>

          <div className="flex items-center gap-2">
            {dashboardQuery.data && (
              <>
                <Button size="sm" onClick={() => setAddOpen(true)}>
                  <IconPlus className="size-3.5" />
                  新增记忆
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    spinOnce(refreshIconRef.current)
                    void queryClient.invalidateQueries({ queryKey: ["fos"] })
                  }}
                >
                  <span ref={refreshIconRef} className="inline-flex">
                    <IconRefresh className="size-3.5" />
                  </span>
                  刷新
                </Button>
              </>
            )}
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="outline"
                    size="icon-sm"
                    aria-label="切换明暗主题"
                    onClick={() => setIsDark((d) => !d)}
                  />
                }
              >
                {isDark ? <IconSun className="size-4" /> : <IconMoon className="size-4" />}
              </TooltipTrigger>
              <TooltipContent>{isDark ? "切换浅色模式" : "切换暗黑模式"}</TooltipContent>
            </Tooltip>
            {healthQuery.data?.auth_enabled && token && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setStoredToken("")
                  setToken("")
                  setAuthError("")
                }}
              >
                <IconLogout className="size-3.5" />
                切换 Token
              </Button>
            )}
          </div>
        </div>
      </header>

      <main className="w-full flex-1 px-4 py-3">
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
            <div className="flex gap-2 border-b border-border pb-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-7 w-28" />
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-24 w-full rounded-lg" />
              ))}
            </div>
            <Skeleton className="h-20 w-full rounded-lg" />
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Skeleton className="h-80 w-full rounded-lg" />
              <Skeleton className="h-80 w-full rounded-lg" />
            </div>
          </div>
        ) : (
          <Tabs value={activeTab} onValueChange={(v) => setActiveTab(String(v))}>
            <TabsList variant="line" className="mb-3 w-full justify-start overflow-x-auto border-b border-border pb-1 text-sm">
              <TabsTrigger value="overview" className="flex-none px-3">总览 Overview</TabsTrigger>
              <TabsTrigger value="proposals" className="flex-none px-3">
                推荐 Inbox <span className="ml-1 font-mono text-xs text-chart-3">({dashboardQuery.data.counts.pending_proposals ?? 0})</span>
              </TabsTrigger>
              <TabsTrigger value="cards" className="flex-none px-3">
                知识卡片 Cards <span className="ml-1 font-mono text-xs text-muted-foreground">({dashboardQuery.data.counts.active_nodes})</span>
              </TabsTrigger>
              <TabsTrigger value="graph" className="flex-none px-3">双时态图谱 Graph</TabsTrigger>
              <TabsTrigger value="playground" className="flex-none px-3">检索 &amp; 照镜子</TabsTrigger>
              <TabsTrigger value="audit" className="flex-none px-3">
                账本 &amp; 门禁 <span className="ml-1 font-mono text-xs text-muted-foreground">({dashboardQuery.data.counts.ledger_atoms})</span>
              </TabsTrigger>
            </TabsList>

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
                onSelectNode={setSelectedNodeId}
                filterPreset={cardFilterPreset}
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
            </div>
          </Tabs>
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
              <Input
                value={addScope}
                onChange={(e) => setAddScope(e.target.value)}
                placeholder="scope (如 global / eng/frontend / eng/ops / fos)"
                className="h-8 flex-1 font-mono text-xs"
              />
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
    </div>
    </TooltipProvider>
  )
}

export default App
