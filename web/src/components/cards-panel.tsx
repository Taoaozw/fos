import { useEffect, useMemo, useRef, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  IconArchive,
  IconCheck,
  IconCopy,
  IconEdit,
  IconPin,
  IconSearch,
  IconShieldCheck,
  IconThumbDown,
  IconThumbUp,
} from "@tabler/icons-react"
import { toast } from "sonner"
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
import { usePanelTransition, useRevealGroup } from "@/lib/motion"
import type { CardFilterPreset, CompiledNode, DashboardResponse } from "@/lib/types"

interface CardsPanelProps {
  dashboard: DashboardResponse
  selectedNodeId: string | null
  onSelectNode: (id: string) => void
  filterPreset?: CardFilterPreset | null
}

function parseEvidenceRaw(raw: string): { prefix: string; lang: string; code: string; suffix: string } | null {
  const m = raw.match(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/)
  if (!m || m.index === undefined) return null
  const prefix = raw.slice(0, m.index).trim()
  const lang = m[1] || "code"
  const code = m[2].replace(/\n$/, "")
  const suffix = raw.slice(m.index + m[0].length).trim()
  return { prefix, lang, code, suffix }
}

function EvidenceBlock({ raw }: { raw: string }) {
  const [copied, setCopied] = useState(false)
  const parsed = useMemo(() => parseEvidenceRaw(raw), [raw])

  if (!parsed) {
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2.5 font-mono text-xs whitespace-pre-wrap text-foreground">
        {raw}
      </div>
    )
  }

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(parsed.code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      toast.error("复制失败")
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {parsed.prefix && (
        <div className="rounded-md border border-border bg-muted/25 p-2.5 font-mono text-xs whitespace-pre-wrap text-muted-foreground">
          {parsed.prefix}
        </div>
      )}
      <div className="overflow-hidden rounded-md border border-border bg-background">
        <div className="flex items-center justify-between border-b border-border bg-muted/40 px-2.5 py-1">
          <Badge variant="outline" className="rounded-md font-mono text-xs">
            {parsed.lang}
          </Badge>
          <Button variant="ghost" size="xs" className="font-normal text-xs" onClick={copyCode}>
            {copied ? <IconCheck className="size-3.5" /> : <IconCopy className="size-3.5" />}
            {copied ? "已复制" : "复制骨架"}
          </Button>
        </div>
        <pre className="max-h-72 overflow-auto p-3 font-mono text-xs leading-relaxed text-foreground">
          <code>{parsed.code}</code>
        </pre>
      </div>
      {parsed.suffix && (
        <div className="rounded-md border border-border bg-muted/25 p-2.5 font-mono text-xs whitespace-pre-wrap text-muted-foreground">
          {parsed.suffix}
        </div>
      )}
    </div>
  )
}

export function CardsPanel({ dashboard, selectedNodeId, onSelectNode, filterPreset }: CardsPanelProps) {
  const [search, setSearch] = useState("")
  const [scope, setScope] = useState("__all__")
  const [status, setStatus] = useState("active")
  const [severity, setSeverity] = useState("__all__")
  const [kind, setKind] = useState("__all__")
  const [special, setSpecial] = useState<"all" | "patterns" | "issues" | "guards">("all")

  useEffect(() => {
    if (!filterPreset) return
    if (filterPreset.scope !== undefined) setScope(filterPreset.scope)
    if (filterPreset.special !== undefined) setSpecial(filterPreset.special)
    setStatus("active")
  }, [filterPreset])

  const [correctTarget, setCorrectTarget] = useState<CompiledNode | null>(null)
  const [correctRule, setCorrectRule] = useState("")
  const [correctReason, setCorrectReason] = useState("")

  const [archiveTarget, setArchiveTarget] = useState<CompiledNode | null>(null)
  const [archiveReason, setArchiveReason] = useState("")

  const queryClient = useQueryClient()
  const listRef = useRef<HTMLDivElement | null>(null)
  const detailRef = useRef<HTMLDivElement | null>(null)

  const filteredNodes = useMemo(() => {
    const q = search.trim().toLowerCase()
    return dashboard.nodes.filter((n) => {
      if (scope !== "__all__" && n.scope !== scope) return false
      if (status !== "__all__" && n.status !== status) return false
      if (severity !== "__all__" && n.severity !== severity) return false
      if (kind !== "__all__" && n.type !== kind) return false
      if (special === "patterns" && n.compat?.pattern !== "code_exemplar") return false
      if (special === "issues" && n.freshness !== "drifted" && n.freshness !== "broken_ref") return false
      if (special === "guards" && !n.guard_pattern) return false
      if (q) {
        const hay = [
          n.id,
          n.rule,
          n.rationale,
          n.guard_hint,
          n.source_ref ?? "",
          ...(n.trigger_when || []),
          ...(n.entities || []),
        ]
          .join(" ")
          .toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [dashboard.nodes, search, scope, status, severity, kind, special])

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

  const feedbackMutation = useMutation({
    mutationFn: (input: { id: string; verdict: "useful" | "noise" }) =>
      fosApi.feedbackNode(input.id, input.verdict),
    onSuccess: (res, vars) => {
      toast.success(
        `${vars.verdict === "useful" ? "已记为有用" : "已记为噪声"}（效用分 ${(res.utility_score * 100).toFixed(0)}%）`
      )
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
  useRevealGroup(listRef, "[data-anim='card-row']", [scope, status, severity, kind, special, filteredNodes.length], {
    y: 5,
    duration: 0.22,
    maxItems: 24,
  })
  usePanelTransition(detailRef, [selectedNodeId, detailNode?.id], { x: 8, y: 0, duration: 0.22 })

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
        <Select
          value={special}
          onValueChange={(v) => setSpecial((v as "all" | "patterns" | "issues" | "guards") ?? "all")}
        >
          <SelectTrigger size="sm" className="w-36 text-xs">
            <SelectValue placeholder="专题" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部专题</SelectItem>
            <SelectItem value="patterns">代码范式 Patterns</SelectItem>
            <SelectItem value="issues">漂移·断链 Issues</SelectItem>
            <SelectItem value="guards">静态守卫 Guards</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Master-Detail Split */}
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-12">
        <div ref={listRef} className="flex flex-col border-y border-border divide-y divide-border lg:col-span-7">
          {filteredNodes.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">
              当前筛选条件下没有匹配的记忆卡片。
            </div>
          ) : (
            filteredNodes.map((n) => {
              const active = n.id === selectedNodeId
              return (
                <div
                  key={n.id}
                  data-anim="card-row"
                  onClick={() => onSelectNode(n.id)}
                  className={`flex cursor-pointer flex-col gap-1.5 px-3 py-2.5 transition-colors hover:bg-accent/40 ${
                    active ? "bg-accent/55" : ""
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-xs font-medium text-foreground">{n.id}</span>
                      <Badge
                        variant={n.severity === "P0" ? "destructive" : "secondary"}
                        className="h-4 rounded-sm px-1.5 font-mono text-[10px]"
                      >
                        {n.severity}
                      </Badge>
                      <span className="font-mono text-[11px] text-muted-foreground">{n.type}</span>
                      <span className="font-mono text-[11px] text-chart-1">{n.scope}</span>
                      {n.status !== "active" && (
                        <Badge variant="secondary" className="h-4 rounded-sm px-1.5 text-[10px]">
                          {n.status}
                        </Badge>
                      )}
                      {n.pinned && (
                        <Badge variant="destructive" className="h-4 rounded-sm px-1.5 text-[10px]">
                          PINNED
                        </Badge>
                      )}
                      {n.compat?.pattern === "code_exemplar" && (
                        <Badge variant="outline" className="h-4 rounded-sm px-1.5 font-mono text-[10px] text-chart-1">
                          pattern{n.compat.lang ? `:${n.compat.lang}` : ""}
                        </Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
                      {n.freshness && n.freshness !== "untracked" && (
                        <span
                          title={`freshness: ${n.freshness}`}
                          className={`size-1.5 rounded-full ${
                            n.freshness === "fresh"
                              ? "bg-chart-2"
                              : n.freshness === "drifted"
                                ? "bg-chart-3"
                                : "bg-chart-5"
                          }`}
                        />
                      )}
                      {(n.recall_hits ?? 0) > 0 && <span>hits:{n.recall_hits}</span>}
                      <span>seen:{n.seen_count} · {(n.valid_from || "").slice(0, 10)}</span>
                    </div>
                  </div>
                  <div className="text-[14px] font-medium leading-6 text-foreground">{n.rule}</div>
                  {n.rationale && (
                    <div className="line-clamp-2 text-[13px] leading-relaxed text-muted-foreground">
                      原因：{n.rationale}
                    </div>
                  )}
                  {n.guard_hint && (
                    <div className="flex items-start gap-1.5 text-xs text-chart-2">
                      <IconShieldCheck className="mt-0.5 size-3.5 shrink-0" />
                      <span className="line-clamp-1">守卫建议：{n.guard_hint}</span>
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>

        {/* Right Detail Panel */}
        <div
          ref={detailRef}
          className="flex flex-col gap-3 border-t border-border pt-3 lg:sticky lg:top-14 lg:col-span-5 lg:border-t-0 lg:border-l lg:pt-0 lg:pl-4"
        >
          {!selectedNodeId ? (
            <div className="py-12 text-center text-sm text-muted-foreground">
              点击左侧任意记忆卡片，查看 L1/L2 无损原文证据、双时态关系，或执行纠偏 / 归档 / 钉选。
            </div>
          ) : detailQuery.isLoading ? (
            <div className="py-12 text-center text-sm text-muted-foreground">
              加载 L2 证据与双时态图谱...
            </div>
          ) : detailNode ? (
            <>
              <div className="flex flex-col gap-2 border-b border-border pb-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant="outline" className="rounded-sm font-mono text-xs">
                      {detailNode.id}
                    </Badge>
                    <Badge
                      variant={detailNode.severity === "P0" ? "destructive" : "secondary"}
                      className="rounded-sm font-mono text-xs"
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
                    {detailNode.graduated && (
                      <Badge variant="secondary" className="rounded-md font-mono text-xs">
                        graduated
                      </Badge>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="xs"
                      disabled={feedbackMutation.isPending}
                      onClick={() =>
                        feedbackMutation.mutate({ id: detailNode.id, verdict: "useful" })
                      }
                    >
                      <IconThumbUp className="size-3.5" />
                      有用
                    </Button>
                    <Button
                      variant="outline"
                      size="xs"
                      disabled={feedbackMutation.isPending}
                      onClick={() =>
                        feedbackMutation.mutate({ id: detailNode.id, verdict: "noise" })
                      }
                    >
                      <IconThumbDown className="size-3.5" />
                      噪声
                    </Button>
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
                <h3 className="text-sm font-medium leading-snug text-foreground">
                  {detailNode.rule}
                </h3>
                <div className="font-mono text-xs text-muted-foreground">
                  Seen {detailNode.seen_count}× · hits {detailNode.recall_hits ?? 0} ·
                  utility {Math.round((detailNode.utility_score ?? 0.5) * 100)}% (+{detailNode.useful_count ?? 0}/-{detailNode.noise_count ?? 0}) ·{" "}
                  {(detailNode.valid_from || "").slice(0, 19)} →{" "}
                  {detailNode.valid_until ? detailNode.valid_until.slice(0, 19) : "present"}
                </div>
              </div>

              <div className="flex flex-col gap-3.5">
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

                {detailNode.guard_pattern && (
                  <div className="flex flex-col gap-1">
                    <div className="text-xs font-medium text-muted-foreground">
                      确定性静态守卫 Guard Pattern (fos guard)
                    </div>
                    <div className="rounded-md border border-border bg-muted/40 px-2.5 py-1.5 font-mono text-xs text-foreground break-all">
                      {detailNode.guard_pattern}
                    </div>
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

                {detailNode.compat && Object.keys(detailNode.compat).length > 0 && (
                  <div className="flex flex-col gap-1.5">
                    <div className="text-xs font-medium text-muted-foreground">
                      框架版本包络 Compat Envelope (SemVer)
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {detailNode.compat.lang && (
                        <Badge variant="outline" className="rounded-md font-mono text-xs">
                          {detailNode.compat.lang}
                        </Badge>
                      )}
                      {Object.entries(detailNode.compat.stack ?? {}).map(([k, v]) => (
                        <Badge key={k} variant="secondary" className="rounded-md font-mono text-xs">
                          {k} {v}
                        </Badge>
                      ))}
                    </div>
                    {detailNode.compat.bench_cmd && (
                      <div className="rounded-md border border-border bg-muted/40 px-2.5 py-1.5 font-mono text-xs text-foreground break-all">
                        verify: {detailNode.compat.bench_cmd}
                      </div>
                    )}
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
                  <ScrollArea className="max-h-96">
                    <div className="flex flex-col gap-2.5 pr-2">
                      {(detailQuery.data?.evidence ?? []).map((a) => (
                        <div key={a.id} className="flex flex-col gap-1.5">
                          <div className="font-mono text-xs text-muted-foreground">
                            {a.id} · {a.source} · {a.ts}
                          </div>
                          <EvidenceBlock raw={a.raw} />
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                </div>
              </div>
            </>
          ) : (
            <div className="py-10 text-center text-sm text-destructive">
              未能加载节点详情
            </div>
          )}
        </div>
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
