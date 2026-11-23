import { useEffect, useMemo, useRef, useState } from "react"
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useQueryStates } from "nuqs"
import {
  IconArchive,
  IconArrowUpRight,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconEdit,
  IconGitMerge,
  IconMinus,
  IconRadar2,
  IconShieldCheck,
  IconSparkles,
  IconX,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { fosApi } from "@/lib/api"
import { useRevealGroup } from "@/lib/motion"
import {
  proposalsFilterParsers,
  type ProposalKindFilter,
  type ProposalStatusFilter,
} from "@/lib/url-state"
import type { Proposal } from "@/lib/types"

interface ProposalsPanelProps {
  onJumpToCard: (nodeId: string) => void
}

interface AcceptPayload {
  id: string
  rule?: string
  rationale?: string
  guard_hint?: string
  severity?: string
  trigger_when?: string[]
  force?: boolean
}

const KIND_META: Record<
  string,
  {
    label: string
    icon: typeof IconSparkles
    stripeClass: string
    badgeClass: string
    order: number
  }
> = {
  fix_drifted: {
    label: "巡检修正",
    icon: IconEdit,
    stripeClass: "border-l-chart-3",
    badgeClass: "border-chart-3/35 bg-chart-3/12 text-chart-3",
    order: 0,
  },
  promote_global: {
    label: "晋升全局",
    icon: IconSparkles,
    stripeClass: "border-l-chart-1",
    badgeClass: "border-chart-1/35 bg-chart-1/12 text-chart-1",
    order: 1,
  },
  merge_dedup: {
    label: "合并瘦身",
    icon: IconGitMerge,
    stripeClass: "border-l-chart-4",
    badgeClass: "border-chart-4/35 bg-chart-4/12 text-chart-4",
    order: 2,
  },
  retire_stale: {
    label: "建议归档",
    icon: IconArchive,
    stripeClass: "border-l-muted-foreground/40",
    badgeClass: "border-border bg-muted/60 text-muted-foreground",
    order: 3,
  },
}

const STATUS_LABEL: Record<string, string> = {
  pending: "待审",
  accepted: "已采纳",
  dismissed: "已忽略",
  expired: "已过期",
  all: "全部状态",
}

const SEVERITY_LABEL: Record<string, string> = {
  P0: "P0 (进 L0)",
  P1: "P1 (按需召回)",
  P2: "P2",
}

function SelectionCheckbox({
  checked,
  indeterminate = false,
  disabled = false,
  onChange,
  ariaLabel,
}: {
  checked: boolean
  indeterminate?: boolean
  disabled?: boolean
  onChange: () => void
  ariaLabel: string
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={indeterminate ? "mixed" : checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation()
        onChange()
      }}
      className={`flex size-4 shrink-0 items-center justify-center rounded-[4px] border transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50 ${
        checked || indeterminate
          ? "border-primary bg-primary text-primary-foreground"
          : "border-input bg-background hover:border-foreground/40"
      }`}
    >
      {indeterminate ? (
        <IconMinus className="size-3 stroke-[2.5]" />
      ) : checked ? (
        <IconCheck className="size-3 stroke-[2.5]" />
      ) : null}
    </button>
  )
}

function ProposalCard({
  proposal: p,
  l0Used,
  l0Limit,
  selected,
  selectable,
  busy,
  onToggleSelect,
  onAccept,
  onDismiss,
  onJumpToCard,
}: {
  proposal: Proposal
  l0Used: number
  l0Limit: number
  selected: boolean
  selectable: boolean
  busy: boolean
  onToggleSelect: (id: string) => void
  onAccept: (payload: AcceptPayload) => void
  onDismiss: (id: string) => void
  onJumpToCard: (nodeId: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editRule, setEditRule] = useState(p.proposed_rule)
  const [editRationale, setEditRationale] = useState(p.proposed_rationale)
  const [editGuard, setEditGuard] = useState(p.proposed_guard_hint)
  const [editSeverity, setEditSeverity] = useState<string>(p.proposed_severity)
  const [editTriggers, setEditTriggers] = useState(
    p.proposed_trigger_when.join(", ")
  )

  const km = KIND_META[p.kind] ?? {
    label: p.kind,
    icon: IconSparkles,
    stripeClass: "border-l-border",
    badgeClass: "border-border bg-secondary text-secondary-foreground",
    order: 9,
  }
  const KindIcon = km.icon
  const deltaText =
    p.estimated_l0_delta > 0
      ? `+${p.estimated_l0_delta}t L0`
      : p.estimated_l0_delta < 0
      ? `${p.estimated_l0_delta}t L0`
      : "0t L0"
  const deltaClass =
    p.estimated_l0_delta > 0
      ? "text-chart-3"
      : p.estimated_l0_delta < 0
      ? "text-chart-2"
      : "text-muted-foreground"
  const wouldExceedL0 =
    p.status === "pending" &&
    p.proposed_scope === "global" &&
    p.proposed_severity === "P0" &&
    p.estimated_l0_delta > 0 &&
    l0Used + p.estimated_l0_delta > l0Limit

  const openEditor = () => {
    setEditRule(p.proposed_rule)
    setEditRationale(p.proposed_rationale)
    setEditGuard(p.proposed_guard_hint)
    setEditSeverity(p.proposed_severity)
    setEditTriggers(p.proposed_trigger_when.join(", "))
    setEditing(true)
  }

  const evidenceCount = p.evidence_nodes.length + p.evidence_manifests.length
  const showTitleKicker =
    Boolean(p.title) && p.title.trim() !== p.proposed_rule.trim()

  return (
    <div
      data-slot="proposal-card"
      className={`surface-card interactive-card group border-l-[3px] ${
        km.stripeClass
      } ${
        selected
          ? "ring-1 ring-primary/40 border-primary/45 bg-accent/20"
          : "hover:border-border"
      }`}
    >
      <div className="flex flex-col gap-3 p-4">
        {/* Top Meta Bar */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {selectable && (
              <SelectionCheckbox
                checked={selected}
                disabled={busy}
                onChange={() => onToggleSelect(p.id)}
                ariaLabel={`选择提案 ${p.id}`}
              />
            )}
            <Badge
              variant="outline"
              className={`h-5 gap-1 rounded-md px-2 text-[11px] font-medium ${km.badgeClass}`}
            >
              <KindIcon className="size-3" />
              {km.label}
            </Badge>
            <Badge
              variant={p.proposed_severity === "P0" ? "destructive" : "secondary"}
              className="h-5 rounded-sm px-1.5 font-mono text-[11px]"
            >
              {p.proposed_severity}
            </Badge>
            <span className="font-mono text-xs font-medium text-chart-1">
              {p.proposed_scope}
            </span>
            <span className="text-border">·</span>
            <span className={`font-mono text-[11px] font-medium ${deltaClass}`}>
              {deltaText}
            </span>
            <span className="text-border">·</span>
            <span className="font-mono text-[11px] text-muted-foreground">
              置信度 {Math.round(p.confidence * 100)}%
            </span>
            {p.status !== "pending" && (
              <Badge variant="secondary" className="h-5 text-[11px]">
                {STATUS_LABEL[p.status] ?? p.status}
              </Badge>
            )}
          </div>
          <span className="font-mono text-[11px] text-muted-foreground/75">
            {p.id}
          </span>
        </div>

        {/* Body */}
        {editing ? (
          <div className="flex flex-col gap-2.5 rounded-lg border border-border bg-muted/35 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={editSeverity}
                onValueChange={(v) => setEditSeverity(v ?? "P1")}
              >
                <SelectTrigger size="sm" className="w-32 font-mono text-xs">
                  <SelectValue>
                    {(v: string | null) => (v ? SEVERITY_LABEL[v] ?? v : "优先级")}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="P0">P0 (进 L0)</SelectItem>
                  <SelectItem value="P1">P1 (按需召回)</SelectItem>
                  <SelectItem value="P2">P2</SelectItem>
                </SelectContent>
              </Select>
              <Input
                value={editTriggers}
                onChange={(e) => setEditTriggers(e.target.value)}
                placeholder="触发关键词（逗号分隔）"
                className="h-8 flex-1 text-xs"
              />
            </div>
            <Textarea
              rows={3}
              value={editRule}
              onChange={(e) => setEditRule(e.target.value)}
              placeholder="规则正文"
              className="text-xs"
            />
            <Input
              value={editRationale}
              onChange={(e) => setEditRationale(e.target.value)}
              placeholder="结构性原因"
              className="h-8 text-xs"
            />
            <Input
              value={editGuard}
              onChange={(e) => setEditGuard(e.target.value)}
              placeholder="自动化守卫建议（可选）"
              className="h-8 text-xs"
            />
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEditing(false)}
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={busy}
                onClick={() => {
                  onAccept({
                    id: p.id,
                    rule: editRule,
                    rationale: editRationale,
                    guard_hint: editGuard,
                    severity: editSeverity,
                    trigger_when: editTriggers
                      .split(/[,，]/)
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })
                  setEditing(false)
                }}
              >
                <IconCheck className="size-3.5" />
                保存并采纳
              </Button>
            </div>
          </div>
        ) : (
          <div className={`flex flex-col gap-2 ${selectable ? "pl-6" : ""}`}>
            {showTitleKicker && (
              <div className="text-xs font-medium text-muted-foreground">
                {p.title}
              </div>
            )}
            <div className="text-[15px] font-semibold leading-snug text-foreground">
              {p.proposed_rule}
            </div>
            {p.proposed_rationale && (
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                {p.proposed_rationale}
              </p>
            )}
            {p.proposed_guard_hint && (
              <div className="flex items-start gap-1.5 rounded-lg border border-border/70 bg-muted/35 px-3 py-2 text-xs text-foreground/85">
                <IconShieldCheck className="mt-0.5 size-3.5 shrink-0 text-chart-2" />
                <span>{p.proposed_guard_hint}</span>
              </div>
            )}
            {p.proposed_trigger_when.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                {p.proposed_trigger_when.map((t) => (
                  <span
                    key={t}
                    className="rounded-md bg-muted/60 px-2 py-0.5 text-[11px] text-muted-foreground"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Footer: Evidence + Actions */}
        {!editing && (
          <div
            className={`flex flex-wrap items-center justify-between gap-2 border-t border-border/70 pt-2.5 ${
              selectable ? "pl-6" : ""
            }`}
          >
            <div className="flex flex-wrap items-center gap-1.5">
              {p.evidence_repos.map((r) => (
                <Badge
                  key={r}
                  variant="outline"
                  className="h-5 rounded-md font-mono text-[11px] text-muted-foreground"
                >
                  {r}
                </Badge>
              ))}
              {evidenceCount > 0 && (
                <Button
                  size="xs"
                  variant="ghost"
                  className="h-6 px-2 text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => setExpanded((v) => !v)}
                >
                  {expanded ? (
                    <>
                      收起证据 <IconChevronUp className="size-3.5" />
                    </>
                  ) : (
                    <>
                      证据 ({evidenceCount}) <IconChevronDown className="size-3.5" />
                    </>
                  )}
                </Button>
              )}
              {p.result_node_id && (
                <Button
                  size="xs"
                  variant="ghost"
                  className="h-6 px-2 text-xs text-chart-1"
                  onClick={() => onJumpToCard(p.result_node_id!)}
                >
                  产物 {p.result_node_id}
                  <IconArrowUpRight className="size-3" />
                </Button>
              )}
              {wouldExceedL0 && (
                <span className="text-xs font-medium text-destructive">
                  超出 300t L0 预算，建议降为 P1
                </span>
              )}
            </div>

            {p.status === "pending" && (
              <div className="flex flex-wrap items-center gap-1.5">
                <Button
                  size="xs"
                  variant="ghost"
                  className="text-muted-foreground hover:text-foreground"
                  disabled={busy}
                  onClick={() => onDismiss(p.id)}
                >
                  <IconX className="size-3.5" />
                  忽略
                </Button>
                <Button size="xs" variant="outline" disabled={busy} onClick={openEditor}>
                  <IconEdit className="size-3.5" />
                  编辑
                </Button>
                {wouldExceedL0 && (
                  <Button
                    size="xs"
                    disabled={busy}
                    onClick={() => onAccept({ id: p.id, severity: "P1" })}
                  >
                    <IconCheck className="size-3.5" />
                    降为 P1 采纳
                  </Button>
                )}
                <Button
                  size="xs"
                  variant={wouldExceedL0 ? "outline" : "default"}
                  disabled={busy}
                  onClick={() => onAccept({ id: p.id, force: wouldExceedL0 })}
                >
                  <IconCheck className="size-3.5" />
                  {wouldExceedL0 ? "强制 P0 采纳" : "采纳"}
                </Button>
              </div>
            )}
          </div>
        )}

        {expanded && (
          <div
            className={`flex flex-col gap-2 rounded-lg border border-border/70 bg-muted/25 p-3 text-xs ${
              selectable ? "ml-6" : ""
            }`}
          >
            {p.evidence_nodes.map((n) => (
              <div key={n.id} className="flex items-start justify-between gap-2">
                <div className="flex-1 leading-relaxed">
                  <span className="mr-1.5 font-mono font-medium text-foreground">
                    [{n.id} · {n.scope}]
                  </span>
                  <span className="text-muted-foreground">{n.rule}</span>
                </div>
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={() => onJumpToCard(n.id)}
                >
                  卡片
                  <IconArrowUpRight className="size-3" />
                </Button>
              </div>
            ))}
            {p.evidence_manifests.map((m) => (
              <div key={m.repo} className="flex flex-wrap items-center gap-1.5">
                <Badge variant="outline" className="font-mono text-xs">
                  manifest:{m.repo}
                </Badge>
                <span className="text-muted-foreground">
                  {m.stack_tags.join(" · ")}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export function ProposalsPanel({ onJumpToCard }: ProposalsPanelProps) {
  const [{ pstatus: statusFilter, pkind: kindFilter }, setProposalFilters] =
    useQueryStates(proposalsFilterParsers)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [batchProgress, setBatchProgress] = useState<{
    action: "accept" | "dismiss"
    done: number
    total: number
  } | null>(null)
  const queryClient = useQueryClient()

  const proposalsQuery = useQuery({
    queryKey: ["fos", "proposals", statusFilter],
    queryFn: () => fosApi.listProposals(statusFilter),
    placeholderData: keepPreviousData,
  })

  const scanMutation = useMutation({
    mutationFn: (opts: { reasoner?: string; force?: boolean }) =>
      fosApi.scanProposals(opts),
    onSuccess: (res) => {
      toast.success(
        `扫描完成（${res.owned_repos.length} 个自有仓库）：新增 ${res.created} 条，当前待审 ${res.pending_count} 条`
      )
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const acceptMutation = useMutation({
    mutationFn: ({ id, ...body }: AcceptPayload) =>
      fosApi.acceptProposal(id, body),
    onSuccess: (res, vars) => {
      setSelectedIds((prev) => {
        if (!prev.has(vars.id)) return prev
        const next = new Set(prev)
        next.delete(vars.id)
        return next
      })
      toast.success(
        res.result_node_id
          ? `已采纳并写入账本：${res.result_node_id}`
          : "已采纳并完成归档"
      )
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const dismissMutation = useMutation({
    mutationFn: (id: string) => fosApi.dismissProposal(id, "用户手动忽略"),
    onSuccess: (_, id) => {
      setSelectedIds((prev) => {
        if (!prev.has(id)) return prev
        const next = new Set(prev)
        next.delete(id)
        return next
      })
      toast.success("已忽略该推荐（同证据集不再重复提示）")
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const data = proposalsQuery.data
  const allProposals = data?.proposals ?? []

  const kindCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const p of allProposals) {
      counts[p.kind] = (counts[p.kind] ?? 0) + 1
    }
    return counts
  }, [allProposals])

  const visibleProposals = useMemo(() => {
    const filtered =
      kindFilter === "all"
        ? allProposals
        : allProposals.filter((p) => p.kind === kindFilter)
    return [...filtered].sort((a, b) => {
      const sevRank = (s: string) => (s === "P0" ? 0 : s === "P1" ? 1 : 2)
      const sa = sevRank(a.proposed_severity)
      const sb = sevRank(b.proposed_severity)
      if (sa !== sb) return sa - sb
      const ka = KIND_META[a.kind]?.order ?? 9
      const kb = KIND_META[b.kind]?.order ?? 9
      if (ka !== kb) return ka - kb
      return b.confidence - a.confidence
    })
  }, [allProposals, kindFilter])

  const visiblePendingIds = useMemo(
    () => visibleProposals.filter((p) => p.status === "pending").map((p) => p.id),
    [visibleProposals]
  )

  useEffect(() => {
    setSelectedIds(new Set())
  }, [statusFilter, kindFilter])

  useEffect(() => {
    const valid = new Set(visiblePendingIds)
    setSelectedIds((prev) => {
      let changed = false
      const next = new Set<string>()
      for (const id of prev) {
        if (valid.has(id)) next.add(id)
        else changed = true
      }
      return changed ? next : prev
    })
  }, [visiblePendingIds])

  const selectedVisibleCount = selectedIds.size
  const allVisibleSelected =
    visiblePendingIds.length > 0 &&
    selectedVisibleCount === visiblePendingIds.length
  const someVisibleSelected =
    selectedVisibleCount > 0 && selectedVisibleCount < visiblePendingIds.length

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (allVisibleSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(visiblePendingIds))
    }
  }

  const runBatch = async (action: "accept" | "dismiss") => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0 || batchProgress) return
    setBatchProgress({ action, done: 0, total: ids.length })

    let ok = 0
    const failedIds: string[] = []
    let firstError = ""

    for (let i = 0; i < ids.length; i++) {
      const id = ids[i]
      try {
        if (action === "accept") {
          await fosApi.acceptProposal(id, {})
        } else {
          await fosApi.dismissProposal(id, "批量手动忽略")
        }
        ok += 1
      } catch (err) {
        failedIds.push(id)
        if (!firstError) {
          firstError = err instanceof Error ? err.message : String(err)
        }
      }
      setBatchProgress({ action, done: i + 1, total: ids.length })
    }

    setBatchProgress(null)
    setSelectedIds(new Set(failedIds))
    void queryClient.invalidateQueries({ queryKey: ["fos"] })

    const verb = action === "accept" ? "采纳" : "忽略"
    if (failedIds.length === 0) {
      toast.success(`批量${verb}完成：共 ${ok} 条`)
    } else if (ok > 0) {
      toast.warning(
        `批量${verb}部分完成：成功 ${ok} 条，失败 ${failedIds.length} 条（${firstError}）`
      )
    } else {
      toast.error(`批量${verb}失败（${failedIds.length} 条）：${firstError}`)
    }
  }

  const listRef = useRef<HTMLDivElement | null>(null)
  useRevealGroup(
    listRef,
    "[data-slot='proposal-card']",
    [statusFilter, kindFilter, visibleProposals.length],
    { y: 6, duration: 0.22 }
  )

  const l0Used = data?.l0_budget.used_tokens ?? 0
  const l0Limit = data?.l0_budget.limit_tokens ?? 300
  const l0Pct = Math.min(100, Math.round((l0Used / Math.max(1, l0Limit)) * 100))
  const isBusy =
    acceptMutation.isPending ||
    dismissMutation.isPending ||
    Boolean(batchProgress)

  const statusSelectLabel = (v: string | null) => {
    if (!v) return "状态"
    if (v === "pending") return `待审 (${data?.counts.pending ?? 0})`
    if (v === "accepted") return `已采纳 (${data?.counts.accepted ?? 0})`
    if (v === "dismissed") return `已忽略 (${data?.counts.dismissed ?? 0})`
    if (v === "expired") return `已过期 (${data?.counts.expired ?? 0})`
    return STATUS_LABEL[v] ?? v
  }

  return (
    <div className="flex flex-col gap-3.5">
      {/* Control & Filter Header */}
      <div className="surface-card flex flex-col gap-3 px-4 py-3.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <Select
              value={statusFilter}
              onValueChange={(v) =>
                void setProposalFilters({
                  pstatus: (v ?? "pending") as ProposalStatusFilter,
                })
              }
            >
              <SelectTrigger size="sm" className="w-36 text-xs">
                <SelectValue>{statusSelectLabel}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pending">
                  待审 ({data?.counts.pending ?? 0})
                </SelectItem>
                <SelectItem value="accepted">
                  已采纳 ({data?.counts.accepted ?? 0})
                </SelectItem>
                <SelectItem value="dismissed">
                  已忽略 ({data?.counts.dismissed ?? 0})
                </SelectItem>
                <SelectItem value="expired">
                  已过期 ({data?.counts.expired ?? 0})
                </SelectItem>
                <SelectItem value="all">全部状态</SelectItem>
              </SelectContent>
            </Select>

            {/* Kind Filter Pills */}
            <div className="flex flex-wrap items-center gap-1.5">
              <Button
                type="button"
                size="xs"
                variant={kindFilter === "all" ? "default" : "secondary"}
                onClick={() => void setProposalFilters({ pkind: "all" })}
                className="h-7 rounded-full px-3 text-xs"
              >
                全部 ({allProposals.length})
              </Button>
              {Object.entries(KIND_META).map(([key, meta]) => {
                const count = kindCounts[key] ?? 0
                if (count === 0 && kindFilter !== key) return null
                const active = kindFilter === key
                return (
                  <Button
                    key={key}
                    type="button"
                    size="xs"
                    variant={active ? "default" : "secondary"}
                    onClick={() =>
                      void setProposalFilters({
                        pkind: (active ? "all" : key) as ProposalKindFilter,
                      })
                    }
                    className="h-7 rounded-full px-3 text-xs"
                  >
                    {meta.label} ({count})
                  </Button>
                )
              })}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>L0 预算</span>
              <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                <div
                  className={`h-full rounded-full transition-all ${
                    l0Pct >= 90 ? "bg-destructive" : "bg-chart-2"
                  }`}
                  style={{ width: `${l0Pct}%` }}
                />
              </div>
              <span className="font-mono text-xs text-foreground">
                {l0Used}/{l0Limit}t
              </span>
            </div>

            <Button
              size="sm"
              variant="outline"
              disabled={scanMutation.isPending}
              onClick={() => scanMutation.mutate({ reasoner: "cli", force: true })}
            >
              <IconRadar2 className="size-3.5" />
              {scanMutation.isPending ? "扫描提炼中..." : "AI 跨仓扫描"}
            </Button>
          </div>
        </div>

        {/* Batch Action Bar (visible when there are pending items) */}
        {visiblePendingIds.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/70 pt-2.5">
            <div className="flex items-center gap-2.5">
              <SelectionCheckbox
                checked={allVisibleSelected}
                indeterminate={someVisibleSelected}
                disabled={isBusy}
                onChange={toggleSelectAll}
                ariaLabel="全选当前待审提案"
              />
              <button
                type="button"
                disabled={isBusy}
                onClick={toggleSelectAll}
                className="text-xs text-muted-foreground transition-colors hover:text-foreground"
              >
                {selectedVisibleCount > 0
                  ? `已选 ${selectedVisibleCount} / ${visiblePendingIds.length} 条`
                  : `全选当前待审 (${visiblePendingIds.length})`}
              </button>
              {selectedVisibleCount > 0 && (
                <button
                  type="button"
                  disabled={isBusy}
                  onClick={() => setSelectedIds(new Set())}
                  className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  清空
                </button>
              )}
            </div>

            {selectedVisibleCount > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                {batchProgress && (
                  <span className="font-mono text-xs text-muted-foreground">
                    正在{batchProgress.action === "accept" ? "采纳" : "忽略"}{" "}
                    {batchProgress.done}/{batchProgress.total}...
                  </span>
                )}
                <Button
                  size="xs"
                  variant="outline"
                  disabled={isBusy}
                  onClick={() => void runBatch("dismiss")}
                >
                  <IconX className="size-3.5" />
                  批量忽略 ({selectedVisibleCount})
                </Button>
                <Button
                  size="xs"
                  disabled={isBusy}
                  onClick={() => void runBatch("accept")}
                >
                  <IconCheck className="size-3.5" />
                  批量采纳 ({selectedVisibleCount})
                </Button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Proposal List */}
      <div
        ref={listRef}
        className={`flex flex-col gap-3 transition-opacity ${
          proposalsQuery.isPlaceholderData ? "opacity-60" : "opacity-100"
        }`}
      >
        {!data || visibleProposals.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center gap-3 py-14 text-center">
              <div className="text-sm text-muted-foreground">
                当前筛选条件下暂无推荐提案
              </div>
              {statusFilter === "pending" && kindFilter === "all" && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={scanMutation.isPending}
                  onClick={() =>
                    scanMutation.mutate({ reasoner: "cli", force: true })
                  }
                >
                  <IconRadar2 className="size-3.5" />
                  立即执行跨仓偏好扫描
                </Button>
              )}
            </CardContent>
          </Card>
        ) : (
          visibleProposals.map((p) => (
            <ProposalCard
              key={p.id}
              proposal={p}
              l0Used={l0Used}
              l0Limit={l0Limit}
              selected={selectedIds.has(p.id)}
              selectable={p.status === "pending"}
              busy={isBusy}
              onToggleSelect={toggleSelect}
              onAccept={(payload) => acceptMutation.mutate(payload)}
              onDismiss={(id) => dismissMutation.mutate(id)}
              onJumpToCard={onJumpToCard}
            />
          ))
        )}
      </div>
    </div>
  )
}
