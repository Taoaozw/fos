import { useMemo, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  IconArchive,
  IconEdit,
  IconPin,
  IconSearch,
  IconShieldCheck,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { fosApi } from "@/lib/api"
import type { CompiledNode, DashboardResponse } from "@/lib/types"

interface CardsPanelProps {
  dashboard: DashboardResponse
  selectedNodeId: string | null
  onSelectNode: (id: string) => void
}

export function CardsPanel({ dashboard, selectedNodeId, onSelectNode }: CardsPanelProps) {
  const [search, setSearch] = useState("")
  const [scope, setScope] = useState("__all__")
  const [status, setStatus] = useState("active")
  const [severity, setSeverity] = useState("__all__")
  const [kind, setKind] = useState("__all__")

  const [correctTarget, setCorrectTarget] = useState<CompiledNode | null>(null)
  const [correctRule, setCorrectRule] = useState("")
  const [correctReason, setCorrectReason] = useState("")

  const [archiveTarget, setArchiveTarget] = useState<CompiledNode | null>(null)
  const [archiveReason, setArchiveReason] = useState("")

  const queryClient = useQueryClient()

  const filteredNodes = useMemo(() => {
    const q = search.trim().toLowerCase()
    return dashboard.nodes.filter((n) => {
      if (scope !== "__all__" && n.scope !== scope) return false
      if (status !== "__all__" && n.status !== status) return false
      if (severity !== "__all__" && n.severity !== severity) return false
      if (kind !== "__all__" && n.type !== kind) return false
      if (q) {
        const hay = [
          n.id,
          n.rule,
          n.rationale,
          n.guard_hint,
          ...(n.trigger_when || []),
          ...(n.entities || []),
        ]
          .join(" ")
          .toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [dashboard.nodes, search, scope, status, severity, kind])

  const detailQuery = useQuery({
    queryKey: ["fos", "node", selectedNodeId],
    queryFn: () => fosApi.getNode(selectedNodeId!, "L2", true),
    enabled: Boolean(selectedNodeId),
  })

  const pinMutation = useMutation({
    mutationFn: (id: string) => fosApi.pinNode(id),
    onSuccess: (res) => {
      toast.success(`已将 ${res.pinned_node} 钉入永久 P0 回归集`)
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const correctMutation = useMutation({
    mutationFn: () => fosApi.correctNode(correctTarget!.id, correctRule, correctReason),
    onSuccess: (res) => {
      toast.success(`已纠偏 ${res.superseded} → ${res.node.id}`)
      setCorrectTarget(null)
      onSelectNode(res.node.id)
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const archiveMutation = useMutation({
    mutationFn: () => fosApi.archiveNode(archiveTarget!.id, archiveReason),
    onSuccess: (res) => {
      toast.success(`已归档 ${res.archived}`)
      setArchiveTarget(null)
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const handleCorrectSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!correctTarget || !correctRule.trim()) return
    correctMutation.mutate()
  }

  const handleArchiveSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!archiveTarget) return
    archiveMutation.mutate()
  }

  const detailNode = detailQuery.data?.node

  return (
    <div className="flex flex-col gap-4">
      {/* Filter Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-56 flex-1">
          <IconSearch className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="过滤规则 / 原因 / 守卫 / 触发词..."
            className="pl-8 text-sm"
          />
        </div>
        <Select value={scope} onValueChange={(v) => setScope(v ?? "__all__")}>
          <SelectTrigger size="sm" className="w-36 font-mono text-xs">
            <SelectValue placeholder="Scope" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">全部 Scope</SelectItem>
            {dashboard.scopes.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={(v) => setStatus(v ?? "__all__")}>
          <SelectTrigger size="sm" className="w-36 text-xs">
            <SelectValue placeholder="状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="active">Active（活跃）</SelectItem>
            <SelectItem value="__all__">全部状态</SelectItem>
            <SelectItem value="superseded">Superseded</SelectItem>
            <SelectItem value="archived">Archived</SelectItem>
          </SelectContent>
        </Select>
        <Select value={severity} onValueChange={(v) => setSeverity(v ?? "__all__")}>
          <SelectTrigger size="sm" className="w-32 text-xs">
            <SelectValue placeholder="优先级" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">全部优先级</SelectItem>
            <SelectItem value="P0">P0 红线</SelectItem>
            <SelectItem value="P1">P1 重要</SelectItem>
            <SelectItem value="P2">P2 窄约束</SelectItem>
          </SelectContent>
        </Select>
        <Select value={kind} onValueChange={(v) => setKind(v ?? "__all__")}>
          <SelectTrigger size="sm" className="w-32 text-xs">
            <SelectValue placeholder="类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">全部类型</SelectItem>
            <SelectItem value="principle">principle</SelectItem>
            <SelectItem value="lesson">lesson</SelectItem>
            <SelectItem value="decision">decision</SelectItem>
            <SelectItem value="thought">thought</SelectItem>
            <SelectItem value="outcome">outcome</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Master-Detail Split */}
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-12">
        <div className="flex flex-col gap-2.5 lg:col-span-7">
          {filteredNodes.length === 0 ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                当前筛选条件下没有匹配的记忆卡片。
              </CardContent>
            </Card>
          ) : (
            filteredNodes.map((n) => {
              const active = n.id === selectedNodeId
              return (
                <Card
                  key={n.id}
                  onClick={() => onSelectNode(n.id)}
                  className={`cursor-pointer gap-2 py-3.5 transition-colors hover:border-foreground/30 ${
                    active ? "border-foreground/50 bg-accent/30" : ""
                  }`}
                >
                  <CardHeader className="px-4 py-0">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant="outline" className="rounded-md font-mono text-xs">
                          {n.id}
                        </Badge>
                        <Badge
                          variant={n.severity === "P0" ? "destructive" : "secondary"}
                          className="rounded-md font-mono text-xs"
                        >
                          {n.severity}
                        </Badge>
                        <Badge variant="outline" className="rounded-md text-xs">
                          {n.type}
                        </Badge>
                        <Badge variant="outline" className="rounded-md font-mono text-xs">
                          {n.scope}
                        </Badge>
                        {n.status !== "active" && (
                          <Badge variant="secondary" className="rounded-md text-xs">
                            {n.status}
                          </Badge>
                        )}
                        {n.pinned && (
                          <Badge variant="destructive" className="rounded-md text-xs">
                            PINNED
                          </Badge>
                        )}
                      </div>
                      <div className="font-mono text-xs text-muted-foreground">
                        seen:{n.seen_count} · {(n.valid_from || "").slice(0, 10)}
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-1.5 px-4 py-0">
                    <div className="text-sm font-medium text-foreground">{n.rule}</div>
                    {n.rationale && (
                      <div className="line-clamp-2 text-xs text-muted-foreground">
                        原因：{n.rationale}
                      </div>
                    )}
                    {n.guard_hint && (
                      <div className="flex items-start gap-1.5 rounded-md border border-border bg-muted/40 px-2.5 py-1.5 text-xs text-foreground">
                        <IconShieldCheck className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                        <span>守卫建议：{n.guard_hint}</span>
                      </div>
                    )}
                  </CardContent>
                </Card>
              )
            })
          )}
        </div>

        {/* Right Detail Panel */}
        <Card className="lg:sticky lg:top-16 lg:col-span-5">
          {!selectedNodeId ? (
            <CardContent className="py-12 text-center text-sm text-muted-foreground">
              点击左侧任意记忆卡片，查看 L1/L2 无损原文证据、双时态关系，或执行纠偏 / 归档 / 钉选。
            </CardContent>
          ) : detailQuery.isLoading ? (
            <CardContent className="py-12 text-center text-sm text-muted-foreground">
              加载 L2 证据与双时态图谱...
            </CardContent>
          ) : detailNode ? (
            <>
              <CardHeader className="gap-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant="outline" className="rounded-md font-mono text-xs">
                      {detailNode.id}
                    </Badge>
                    <Badge
                      variant={detailNode.severity === "P0" ? "destructive" : "secondary"}
                      className="rounded-md font-mono text-xs"
                    >
                      {detailNode.severity}
                    </Badge>
                    <Badge variant="outline" className="rounded-md font-mono text-xs">
                      {detailNode.scope}
                    </Badge>
                    <Badge variant="outline" className="rounded-md text-xs">
                      {detailNode.status}
                    </Badge>
                    {detailNode.freshness && detailNode.freshness !== "untracked" && (
                      <Badge
                        variant={
                          detailNode.freshness === "broken_ref"
                            ? "destructive"
                            : detailNode.freshness === "drifted"
                            ? "secondary"
                            : "outline"
                        }
                        className="rounded-md font-mono text-xs"
                      >
                        {detailNode.freshness}
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="xs"
                      disabled={pinMutation.isPending}
                      onClick={() => pinMutation.mutate(detailNode.id)}
                    >
                      <IconPin className="size-3.5" />
                      Pin
                    </Button>
                    <Button
                      variant="outline"
                      size="xs"
                      onClick={() => {
                        setCorrectTarget(detailNode)
                        setCorrectRule(detailNode.rule)
                        setCorrectReason("")
                      }}
                    >
                      <IconEdit className="size-3.5" />
                      纠偏
                    </Button>
                    <Button
                      variant="destructive"
                      size="xs"
                      onClick={() => {
                        setArchiveTarget(detailNode)
                        setArchiveReason("")
                      }}
                    >
                      <IconArchive className="size-3.5" />
                      归档
                    </Button>
                  </div>
                </div>
                <CardTitle className="text-sm font-medium leading-snug">
                  {detailNode.rule}
                </CardTitle>
                <CardDescription className="font-mono text-xs">
                  Seen {detailNode.seen_count}× ({(detailNode.seen_dates || []).join(", ") || "-"}) ·
                  conf {detailNode.confidence} · {(detailNode.valid_from || "").slice(0, 19)} →{" "}
                  {detailNode.valid_until ? detailNode.valid_until.slice(0, 19) : "present"}
                </CardDescription>
              </CardHeader>

              <CardContent className="flex flex-col gap-4">
                {detailNode.superseded_by && (
                  <div className="flex items-center justify-between rounded-md border border-border bg-muted/40 px-3 py-2 text-xs">
                    <span className="text-muted-foreground">
                      已被新规则替代：{detailNode.superseded_by}
                    </span>
                    <Button
                      variant="outline"
                      size="xs"
                      onClick={() => onSelectNode(detailNode.superseded_by!)}
                    >
                      跳转查看
                    </Button>
                  </div>
                )}

                {detailNode.source_ref && (
                  <div className="flex flex-col gap-1">
                    <div className="text-xs font-medium text-muted-foreground">
                      权威源指针 Source Pointer (Progressive Disclosure)
                    </div>
                    <div className="rounded-md border border-border bg-muted/40 px-2.5 py-1.5 font-mono text-xs text-foreground break-all">
                      {detailNode.source_ref}
                    </div>
                  </div>
                )}

                {detailNode.rationale && (
                  <div className="flex flex-col gap-1">
                    <div className="text-xs font-medium text-muted-foreground">原因 Rationale</div>
                    <div className="text-sm text-foreground">{detailNode.rationale}</div>
                  </div>
                )}

                {detailNode.guard_hint && (
                  <div className="flex flex-col gap-1">
                    <div className="text-xs font-medium text-muted-foreground">
                      守卫建议 Guard Test
                    </div>
                    <div className="rounded-md border border-border bg-muted/40 p-2.5 text-xs text-foreground">
                      {detailNode.guard_hint}
                    </div>
                  </div>
                )}

                <div className="flex flex-col gap-1.5">
                  <div className="text-xs font-medium text-muted-foreground">
                    触发条件 (trigger_when)
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {(detailNode.trigger_when || []).map((t) => (
                      <Badge key={t} variant="secondary" className="rounded-md text-xs">
                        {t}
                      </Badge>
                    ))}
                  </div>
                </div>

                <Separator />

                <div className="flex flex-col gap-1.5">
                  <div className="text-xs font-medium text-muted-foreground">
                    双时态关系边 ({detailQuery.data?.graph?.length ?? 0})
                  </div>
                  {(detailQuery.data?.graph ?? []).length === 0 ? (
                    <div className="text-xs text-muted-foreground">无显式边</div>
                  ) : (
                    <div className="flex flex-col gap-1 font-mono text-xs">
                      {(detailQuery.data?.graph ?? []).map((e, i) => (
                        <div key={i} className="text-foreground">
                          {e.src_id}{" "}
                          <span className="text-muted-foreground">--[{e.edge_type}]--&gt;</span>{" "}
                          {e.dst_id}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <Separator />

                <div className="flex flex-col gap-2">
                  <div className="text-xs font-medium text-muted-foreground">
                    L2 不可变原始证据 ({detailQuery.data?.evidence?.length ?? 0})
                  </div>
                  <ScrollArea className="max-h-56">
                    <div className="flex flex-col gap-2.5 pr-2">
                      {(detailQuery.data?.evidence ?? []).map((a) => (
                        <div key={a.id} className="flex flex-col gap-1">
                          <div className="font-mono text-xs text-muted-foreground">
                            {a.id} · {a.source} · {a.ts}
                          </div>
                          <div className="rounded-md border border-border bg-muted/30 p-2.5 font-mono text-xs whitespace-pre-wrap text-foreground">
                            {a.raw}
                          </div>
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                </div>
              </CardContent>
            </>
          ) : (
            <CardContent className="py-10 text-center text-sm text-destructive">
              未能加载节点详情
            </CardContent>
          )}
        </Card>
      </div>

      {/* Correct Dialog */}
      <Dialog open={Boolean(correctTarget)} onOpenChange={(o) => !o && setCorrectTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>自我纠正 {correctTarget?.id}</DialogTitle>
            <DialogDescription>
              旧节点将被标记为 superseded（默认检索 0% 召回，保留 --as-of 历史），新规则自动继承 kind 与 scope。
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCorrectSubmit} className="flex flex-col gap-3">
            <Textarea
              rows={3}
              value={correctRule}
              onChange={(e) => setCorrectRule(e.target.value)}
              placeholder="输入修正后的精确规则..."
            />
            <Input
              value={correctReason}
              onChange={(e) => setCorrectReason(e.target.value)}
              placeholder="纠偏原因（例如：边界条件更新 / 旧假设证伪）"
            />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setCorrectTarget(null)}>
                取消
              </Button>
              <Button type="submit" disabled={correctMutation.isPending}>
                {correctMutation.isPending ? "保存中..." : "保存纠偏"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Archive Dialog */}
      <Dialog open={Boolean(archiveTarget)} onOpenChange={(o) => !o && setArchiveTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>归档节点 {archiveTarget?.id}</DialogTitle>
            <DialogDescription>
              归档后从 boot / recall / mirror 隐藏，但原始账本与 --as-of 历史永久可查。
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleArchiveSubmit} className="flex flex-col gap-3">
            <Input
              value={archiveReason}
              onChange={(e) => setArchiveReason(e.target.value)}
              placeholder="归档原因（例如：已固化为 CI Guard Test / 功能已下线）"
            />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setArchiveTarget(null)}>
                取消
              </Button>
              <Button type="submit" variant="destructive" disabled={archiveMutation.isPending}>
                {archiveMutation.isPending ? "归档中..." : "确认归档"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
