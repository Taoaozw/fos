import { useRef, useState } from "react"
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import {
  IconArchive,
  IconArrowUpRight,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconEdit,
  IconGitMerge,
  IconRadar2,
  IconSparkles,
  IconX,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
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
import { useRevealGroup } from "@/lib/motion"
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
  { label: string; icon: typeof IconSparkles }
> = {
  promote_global: { label: "晋升全局", icon: IconSparkles },
  merge_dedup: { label: "合并瘦身", icon: IconGitMerge },
  retire_stale: { label: "建议归档", icon: IconArchive },
  fix_drifted: { label: "巡检修正", icon: IconEdit },
}

const STATUS_LABEL: Record<string, string> = {
  pending: "待审",
  accepted: "已采纳",
  dismissed: "已忽略",
  expired: "已过期",
}

function ProposalCard({
  proposal: p,
  l0Used,
  l0Limit,
  busyAccept,
  busyDismiss,
  onAccept,
  onDismiss,
  onJumpToCard,
}: {
  proposal: Proposal
  l0Used: number
  l0Limit: number
  busyAccept: boolean
  busyDismiss: boolean
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

  const km = KIND_META[p.kind] ?? { label: p.kind, icon: IconSparkles }
  const KindIcon = km.icon
  const deltaText =
    p.estimated_l0_delta > 0
      ? `+${p.estimated_l0_delta}t L0`
      : p.estimated_l0_delta < 0
      ? `${p.estimated_l0_delta}t L0`
      : "0t L0 (按需召回)"
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

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="gap-1 text-xs">
              <KindIcon className="size-3" />
              {km.label}
            </Badge>
            <Badge
              variant={p.proposed_severity === "P0" ? "default" : "outline"}
              className="font-mono text-xs"
            >
              {p.proposed_severity}
            </Badge>
            <Badge variant="outline" className="font-mono text-xs">
              {p.proposed_scope}
            </Badge>
            <Badge variant="outline" className="font-mono text-xs">
              {deltaText}
            </Badge>
            {p.status !== "pending" && (
              <Badge variant="secondary" className="text-xs">
                {STATUS_LABEL[p.status] ?? p.status}
              </Badge>
            )}
          </div>
          <div className="font-mono text-xs text-muted-foreground">{p.id}</div>
        </div>
        <CardTitle className="mt-1.5 text-sm font-medium">{p.title}</CardTitle>
      </CardHeader>

      <CardContent className="flex flex-col gap-3">
        {editing ? (
          <div className="flex flex-col gap-2.5 rounded-md border border-border bg-muted/40 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={editSeverity}
                onValueChange={(v) => setEditSeverity(v ?? "P1")}
              >
                <SelectTrigger size="sm" className="w-28 font-mono text-xs">
                  <SelectValue />
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
                disabled={busyAccept}
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
          <>
            <div className="text-sm leading-relaxed text-foreground">
              {p.proposed_rule}
            </div>
            {p.proposed_rationale && (
              <div className="text-xs text-muted-foreground">
                原因：{p.proposed_rationale}
              </div>
            )}
            {p.proposed_guard_hint && (
              <div className="rounded-md bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
                守卫：{p.proposed_guard_hint}
              </div>
            )}
            {p.proposed_trigger_when.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-xs text-muted-foreground">触发场景：</span>
                {p.proposed_trigger_when.map((t) => (
                  <Badge key={t} variant="outline" className="text-xs">
                    {t}
                  </Badge>
                ))}
              </div>
            )}
          </>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground">证据来源：</span>
            {p.evidence_repos.map((r) => (
              <Badge key={r} variant="secondary" className="font-mono text-xs">
                {r}
              </Badge>
            ))}
            {p.result_node_id && (
              <Button
                size="xs"
                variant="ghost"
                onClick={() => onJumpToCard(p.result_node_id!)}
              >
                查看产物 {p.result_node_id}
                <IconArrowUpRight className="size-3" />
              </Button>
            )}
          </div>

          {(p.evidence_nodes.length > 0 || p.evidence_manifests.length > 0) && (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? (
                <>
                  收起证据 <IconChevronUp className="size-3.5" />
                </>
              ) : (
                <>
                  展开证据 ({p.evidence_nodes.length + p.evidence_manifests.length}){" "}
                  <IconChevronDown className="size-3.5" />
                </>
              )}
            </Button>
          )}
        </div>

        {expanded && (
          <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3 text-xs">
            {p.evidence_nodes.map((n) => (
              <div key={n.id} className="flex items-start justify-between gap-2">
                <div className="flex-1">
                  <span className="mr-1.5 font-mono text-foreground">
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

        {p.status === "pending" && !editing && (
          <>
            <Separator />
            <div className="flex flex-wrap items-center justify-between gap-2">
              {wouldExceedL0 ? (
                <div className="text-xs text-destructive">
                  直接按 P0 采纳将超出 300t L0 预算，建议降为 P1 按需召回
                </div>
              ) : (
                <div className="text-xs text-muted-foreground">
                  置信度 {Math.round(p.confidence * 100)}%
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busyDismiss}
                  onClick={() => onDismiss(p.id)}
                >
                  <IconX className="size-3.5" />
                  忽略
                </Button>
                <Button size="sm" variant="outline" onClick={openEditor}>
                  <IconEdit className="size-3.5" />
                  编辑
                </Button>
                {wouldExceedL0 && (
                  <Button
                    size="sm"
                    disabled={busyAccept}
                    onClick={() => onAccept({ id: p.id, severity: "P1" })}
                  >
                    <IconCheck className="size-3.5" />
                    降为 P1 采纳
                  </Button>
                )}
                <Button
                  size="sm"
                  variant={wouldExceedL0 ? "outline" : "default"}
                  disabled={busyAccept}
                  onClick={() => onAccept({ id: p.id, force: wouldExceedL0 })}
                >
                  <IconCheck className="size-3.5" />
                  {wouldExceedL0 ? "强制 P0 采纳" : "采纳"}
                </Button>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function ProposalsPanel({ onJumpToCard }: ProposalsPanelProps) {
  const [statusFilter, setStatusFilter] = useState<string>("pending")
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
    onSuccess: (res) => {
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
    onSuccess: () => {
      toast.success("已忽略该推荐（同证据集不再重复提示）")
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const data = proposalsQuery.data
  const listRef = useRef<HTMLDivElement | null>(null)
  useRevealGroup(listRef, "[data-slot='card']", [statusFilter, data?.proposals.length], {
    y: 8,
    duration: 0.26,
  })
  const l0Used = data?.l0_budget.used_tokens ?? 0
  const l0Limit = data?.l0_budget.limit_tokens ?? 300
  const l0Pct = Math.min(100, Math.round((l0Used / Math.max(1, l0Limit)) * 100))

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
          <div className="flex flex-wrap items-center gap-3">
            <Select
              value={statusFilter}
              onValueChange={(v) => setStatusFilter(v ?? "pending")}
            >
              <SelectTrigger size="sm" className="w-36 text-xs">
                <SelectValue />
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
                <SelectItem value="all">全部</SelectItem>
              </SelectContent>
            </Select>

            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>L0 唤醒内核预算</span>
              <Badge variant="outline" className="font-mono text-xs">
                {l0Used} / {l0Limit} t ({l0Pct}%)
              </Badge>
            </div>
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
        </CardContent>
      </Card>

      <div
        ref={listRef}
        className={`flex flex-col gap-3 transition-opacity ${
          proposalsQuery.isPlaceholderData ? "opacity-60" : "opacity-100"
        }`}
      >
        {!data || data.proposals.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center gap-3 py-14 text-center">
              <div className="text-sm text-muted-foreground">
                当前状态下暂无推荐提案
              </div>
              {statusFilter === "pending" && (
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
          data.proposals.map((p) => (
            <ProposalCard
              key={p.id}
              proposal={p}
              l0Used={l0Used}
              l0Limit={l0Limit}
              busyAccept={acceptMutation.isPending}
              busyDismiss={dismissMutation.isPending}
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
