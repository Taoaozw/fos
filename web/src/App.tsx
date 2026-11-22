import { useEffect, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { ApiError, fosApi, getStoredToken, setStoredToken } from "@/lib/api"

export function App() {
  const [token, setToken] = useState<string>(() => getStoredToken())
  const [authError, setAuthError] = useState<string>("")
  const [activeTab, setActiveTab] = useState<string>("overview")
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [isDark, setIsDark] = useState<boolean>(true)
  const [themeVersion, setThemeVersion] = useState<number>(0)

  const [addOpen, setAddOpen] = useState(false)
  const [addKind, setAddKind] = useState("lesson")
  const [addSeverity, setAddSeverity] = useState("P1")
  const [addScope, setAddScope] = useState("global")
  const [addContent, setAddContent] = useState("")

  const queryClient = useQueryClient()

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

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <Toaster position="bottom-right" richColors />

      <header className="sticky top-0 z-20 border-b border-border bg-card/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-5 py-3">
          <div className="flex items-center gap-2.5">
            <IconBrain className="size-5 text-foreground" />
            <div className="text-sm font-medium">FlowOS</div>
            <Badge variant="outline" className="rounded-md font-mono text-xs">
              snap: {dashboardQuery.data?.snapshot_version ?? healthQuery.data?.snapshot_version ?? "-"}
            </Badge>
            {dashboardQuery.data && (
              <Badge variant="secondary" className="rounded-md font-mono text-xs">
                vec: {dashboardQuery.data.sqlite_vec ? "on" : "off"}
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
                  onClick={() => void queryClient.invalidateQueries({ queryKey: ["fos"] })}
                >
                  <IconRefresh className="size-3.5" />
                  刷新
                </Button>
              </>
            )}
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="切换明暗主题"
              onClick={() => setIsDark((d) => !d)}
            >
              {isDark ? <IconSun className="size-4" /> : <IconMoon className="size-4" />}
            </Button>
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

      <main className="mx-auto w-full max-w-7xl flex-1 px-5 py-5">
        {healthQuery.isError ? (
          <div className="rounded-lg border border-border bg-card p-6 text-sm text-destructive">
            无法连接到 fosd 服务（请确认 `fosd serve` 正在运行）。
          </div>
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
          <div className="py-20 text-center text-sm text-muted-foreground">
            正在加载 FlowOS 大脑状态...
          </div>
        ) : (
          <Tabs value={activeTab} onValueChange={(v) => setActiveTab(String(v))}>
            <TabsList className="mb-4">
              <TabsTrigger value="overview">总览 Overview</TabsTrigger>
              <TabsTrigger value="cards">
                知识卡片 Cards ({dashboardQuery.data.counts.active_nodes})
              </TabsTrigger>
              <TabsTrigger value="graph">双时态图谱 Graph</TabsTrigger>
              <TabsTrigger value="playground">检索 &amp; 照镜子</TabsTrigger>
              <TabsTrigger value="audit">
                账本 &amp; 门禁 ({dashboardQuery.data.counts.ledger_atoms})
              </TabsTrigger>
            </TabsList>

            <TabsContent value="overview">
              <OverviewPanel dashboard={dashboardQuery.data} />
            </TabsContent>

            <TabsContent value="cards">
              <CardsPanel
                dashboard={dashboardQuery.data}
                selectedNodeId={selectedNodeId}
                onSelectNode={setSelectedNodeId}
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
          </Tabs>
        )}
      </main>

      {/* Add Memory Dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>录入新记忆 (fos add)</DialogTitle>
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
                placeholder="scope (如 global / fos / goto)"
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
  )
}

export default App
