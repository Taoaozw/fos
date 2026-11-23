import { useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  IconAlertTriangle,
  IconArrowBackUp,
  IconArrowRight,
  IconArrowUpRight,
  IconBolt,
  IconCheck,
  IconCode,
  IconCopy,
  IconLayersLinked,
  IconSparkles,
  IconThumbUp,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { SegmentedTickBar, type TickSegment } from "@/components/shared/segmented-tick-bar"
import { StatusPill } from "@/components/shared/status-pill"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { fosApi } from "@/lib/api"
import { useCountUp, useMatrixWave, usePanelTransition, useRevealGroup } from "@/lib/motion"
import type { DashboardResponse } from "@/lib/types"

function KpiValue({ value }: { value: number | string }) {
  const ref = useRef<HTMLDivElement | null>(null)
  useCountUp(ref, value)
  return (
    <div ref={ref} className="font-mono text-[26px] font-semibold leading-none tracking-tight text-foreground">
      {value}
    </div>
  )
}

interface OverviewPanelProps {
  dashboard: DashboardResponse
  onJumpToCard: (nodeId: string) => void
  onFilterCards: (opts: { scope?: string; special?: "all" | "patterns" | "issues" | "guards" }) => void
}

function DotMeter({
  ratio,
  colorClass,
  dots = 10,
}: {
  ratio: number
  colorClass: string
  dots?: number
}) {
  const filled = Math.max(ratio > 0 ? 1 : 0, Math.round(Math.min(1, Math.max(0, ratio)) * dots))
  return (
    <div className="flex items-center gap-[3px]" aria-hidden="true">
      {Array.from({ length: dots }).map((_, i) => (
        <span
          key={i}
          className={`h-2.5 w-1 rounded-full transition-colors ${
            i < filled ? colorClass : "bg-muted-foreground/18"
          }`}
        />
      ))}
    </div>
  )
}

function DistributionList({
  title,
  entries,
  barClass,
  onClickKey,
}: {
  title: string
  entries: Array<[string, number]>
  barClass: string
  onClickKey?: (key: string) => void
}) {
  const max = Math.max(1, ...entries.map(([, v]) => v))
  const total = Math.max(1, entries.reduce((acc, [, v]) => acc + v, 0))
  return (
    <div className="flex flex-col gap-2">
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      {entries.length === 0 ? (
        <div className="surface-inset px-3.5 py-3 text-xs text-muted-foreground">暂无活跃数据</div>
      ) : (
        <div className="surface-inset divide-y divide-border/60 overflow-hidden">
          {entries.map(([key, val]) => {
            const pct = Math.round((val / total) * 100)
            return (
              <div
                key={key}
                onClick={onClickKey ? () => onClickKey(key) : undefined}
                className={`group flex items-center gap-3 px-3.5 py-2 text-xs transition-colors duration-150 ${
                  onClickKey ? "cursor-pointer hover:bg-card/85" : ""
                }`}
              >
                <span className={`size-2 shrink-0 rounded-full ${barClass}`} />
                <div className="min-w-0 flex-1 truncate font-mono font-medium text-foreground">
                  {key}
                </div>
                <div className="w-8 text-right font-mono text-muted-foreground">{val}</div>
                <DotMeter ratio={val / max} colorClass={barClass} dots={10} />
                <div className="w-9 text-right font-mono text-[11px] text-muted-foreground">
                  {pct}%
                </div>
                {onClickKey && (
                  <IconArrowUpRight className="size-3.5 shrink-0 text-muted-foreground/40 transition-all duration-150 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-foreground" />
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

const BOOT_SCOPE_COLORS = [
  "bg-chart-4",
  "bg-chart-1",
  "bg-chart-2",
  "bg-chart-3",
  "bg-chart-5",
]

export function OverviewPanel({ dashboard, onJumpToCard, onFilterCards }: OverviewPanelProps) {
  const [bootProject, setBootProject] = useState<string>("__global__")
  const [copiedBoot, setCopiedBoot] = useState(false)
  const queryClient = useQueryClient()
  const rootRef = useRef<HTMLDivElement | null>(null)
  const matrixRef = useRef<HTMLDivElement | null>(null)
  const bootBoxRef = useRef<HTMLDivElement | null>(null)

  const bootQuery = useQuery({
    queryKey: ["fos", "boot", bootProject],
    queryFn: () => fosApi.boot(bootProject === "__global__" ? undefined : bootProject, 300),
  })

  const undoMutation = useMutation({
    mutationFn: () => fosApi.undo(),
    onSuccess: (res) => {
      toast.success(`已原子回滚快照：${res.previous} → ${res.current}`)
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const activeNodes = useMemo(
    () => dashboard.nodes.filter((n) => n.status === "active"),
    [dashboard.nodes]
  )

  const freshnessSegments = useMemo<TickSegment[]>(() => {
    let fresh = 0
    let drifted = 0
    let broken = 0
    let untracked = 0
    for (const n of activeNodes) {
      if (n.freshness === "fresh") fresh += 1
      else if (n.freshness === "drifted") drifted += 1
      else if (n.freshness === "broken_ref") broken += 1
      else untracked += 1
    }
    return [
      { key: "fresh", label: "fresh", value: fresh, colorClass: "bg-chart-2" },
      {
        key: "drifted",
        label: "drifted",
        value: drifted,
        colorClass: "bg-chart-3",
        onClick: drifted > 0 ? () => onFilterCards({ scope: "__all__", special: "issues" }) : undefined,
      },
      {
        key: "broken_ref",
        label: "broken_ref",
        value: broken,
        colorClass: "bg-chart-5",
        onClick: broken > 0 ? () => onFilterCards({ scope: "__all__", special: "issues" }) : undefined,
      },
      { key: "untracked", label: "untracked", value: untracked, colorClass: "bg-muted-foreground/35" },
    ]
  }, [activeNodes, onFilterCards])

  const typeSegments = useMemo<TickSegment[]>(() => {
    const patterns = activeNodes.filter((n) => n.compat?.pattern === "code_exemplar").length
    const byType = dashboard.distributions.by_type
    const nonPatternDecisions = Math.max(0, (byType.decision ?? 0) - patterns)
    return [
      { key: "principle", label: "principle", value: byType.principle ?? 0, colorClass: "bg-chart-4" },
      {
        key: "pattern",
        label: "pattern",
        value: patterns,
        colorClass: "bg-chart-1",
        onClick: patterns > 0 ? () => onFilterCards({ scope: "__all__", special: "patterns" }) : undefined,
      },
      { key: "decision", label: "decision", value: nonPatternDecisions, colorClass: "bg-chart-2" },
      { key: "lesson", label: "lesson", value: byType.lesson ?? 0, colorClass: "bg-chart-3" },
      {
        key: "other",
        label: "thought/other",
        value: (byType.thought ?? 0) + (byType.outcome ?? 0),
        colorClass: "bg-muted-foreground/35",
      },
    ]
  }, [activeNodes, dashboard.distributions.by_type, onFilterCards])

  const bootUsed = bootQuery.data?.used_tokens ?? 0
  const bootBudget = bootQuery.data?.budget_tokens ?? 300
  const bootSegments = useMemo<TickSegment[]>(() => {
    const entries = bootQuery.data?.entries ?? []
    if (entries.length === 0) return []
    const weights = entries.map((e) => Math.max(1, e.l0.length))
    const totalW = weights.reduce((a, b) => a + b, 0) || 1
    const byScope = new Map<string, { tokens: number; count: number; pinned: boolean; sampleId: string }>()
    entries.forEach((entry, idx) => {
      const est = Math.max(1, Math.round((weights[idx] / totalW) * Math.max(bootUsed, entries.length)))
      const prev = byScope.get(entry.scope) ?? {
        tokens: 0,
        count: 0,
        pinned: false,
        sampleId: entry.id,
      }
      prev.tokens += est
      prev.count += 1
      if (entry.severity === "P0") prev.pinned = true
      byScope.set(entry.scope, prev)
    })
    return [...byScope.entries()].map(([sc, info], idx) => ({
      key: sc,
      label: sc,
      value: info.tokens,
      displayValue: `${info.tokens}t`,
      colorClass: BOOT_SCOPE_COLORS[idx % BOOT_SCOPE_COLORS.length],
      pinned: info.pinned,
      onClick: () => onFilterCards({ scope: sc, special: "all" }),
    }))
  }, [bootQuery.data?.entries, bootUsed, onFilterCards])

  const c = dashboard.counts
  const h = dashboard.hygiene
  const codePatterns =
    h?.code_patterns_count ?? activeNodes.filter((n) => n.compat?.pattern === "code_exemplar").length
  const alertsCount = (h?.drifted_nodes ?? 0) + (h?.broken_ref_nodes ?? 0)

  const kpis = [
    {
      label: "活跃记忆 Active",
      value: c.active_nodes,
      sub: `总节点 ${c.total_nodes} · L1 均长 ${h?.avg_rule_tokens ?? "-"}t`,
      dotClass: "bg-chart-2",
      onClick: () => onFilterCards({ scope: "__all__", special: "all" }),
    },
    {
      label: "代码范式 Patterns",
      value: codePatterns,
      sub: "符号锚定 + SemVer 包络",
      dotClass: "bg-chart-1",
      onClick: () => onFilterCards({ scope: "__all__", special: "patterns" }),
    },
    {
      label: "原文指针覆盖",
      value: h ? `${h.pointer_coverage_pct}%` : "-",
      sub: `source_ref ${h?.pointer_nodes ?? 0} · 超胖 ${h?.bloated_nodes ?? 0}`,
      dotClass: "bg-chart-4",
      onClick: undefined,
    },
    {
      label: "抗腐化告警",
      value: alertsCount,
      sub: `漂移 ${h?.drifted_nodes ?? 0} · 断链 ${h?.broken_ref_nodes ?? 0}`,
      dotClass: alertsCount > 0 ? "bg-destructive" : "bg-muted-foreground/40",
      highlight: alertsCount > 0 ? "text-destructive" : "",
      onClick: alertsCount > 0 ? () => onFilterCards({ scope: "__all__", special: "issues" }) : undefined,
    },
    {
      label: "不可变账本 Atoms",
      value: c.ledger_atoms,
      sub: `纠偏 ${c.superseded_nodes} · 归档 ${c.archived_nodes}`,
      dotClass: "bg-muted-foreground/45",
      onClick: undefined,
    },
    {
      label: "待审推荐 Inbox",
      value: c.pending_proposals ?? 0,
      sub: `关系边 ${c.edges} · 金标 ${c.golden_cases}`,
      dotClass: (c.pending_proposals ?? 0) > 0 ? "bg-chart-3" : "bg-muted-foreground/40",
      highlight: (c.pending_proposals ?? 0) > 0 ? "text-chart-3" : "",
      onClick: undefined,
    },
  ]

  const scopeEntries = Object.entries(dashboard.distributions.by_scope).sort((a, b) => b[1] - a[1])
  const sevEntries = Object.entries(dashboard.distributions.by_severity)
  const projectScopes = dashboard.scopes.filter((s) => s !== "global")

  useRevealGroup(rootRef, "[data-anim='kpi']", [dashboard.snapshot_version], { y: 7, duration: 0.34 })
  useMatrixWave(matrixRef, "[data-anim='dot']", [dashboard.snapshot_version, activeNodes.length])
  usePanelTransition(bootBoxRef, [bootProject, bootQuery.data?.kernel_text], { y: 4, duration: 0.24 })

  const copyBootKernel = async () => {
    const text = bootQuery.data?.kernel_text
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopiedBoot(true)
      setTimeout(() => setCopiedBoot(false), 1500)
    } catch {
      toast.error("复制失败")
    }
  }

  return (
    <div ref={rootRef} className="flex flex-col gap-4">
      {/* KPI Cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {kpis.map((item) => (
          <div
            key={item.label}
            data-anim="kpi"
            onClick={item.onClick}
            className={`surface-card group flex flex-col justify-between gap-2.5 px-4 py-3.5 ${
              item.onClick ? "interactive-card cursor-pointer hover:border-foreground/20" : ""
            }`}
          >
            <div className="flex items-center justify-between gap-1.5 text-xs font-medium text-muted-foreground">
              <span className="inline-flex min-w-0 items-center gap-1.5">
                <span className={`size-1.5 shrink-0 rounded-full ${item.dotClass}`} />
                <span className="truncate">{item.label}</span>
              </span>
              {item.onClick && (
                <IconArrowUpRight className="size-3.5 shrink-0 text-muted-foreground/45 transition-all duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-foreground" />
              )}
            </div>
            <div className={item.highlight ?? ""}>
              <KpiValue value={item.value} />
            </div>
            <div className="truncate text-[11px] text-muted-foreground">{item.sub}</div>
          </div>
        ))}
      </div>

      {/* Memory Matrix */}
      <div ref={matrixRef} className="surface-card px-5 py-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="flex items-baseline gap-2">
            <span className="font-semibold tracking-tight text-foreground">Memory Matrix</span>
            <span className="font-mono text-[11px] text-muted-foreground">
              {activeNodes.length} active · {codePatterns} patterns · {alertsCount} alerts
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-[2.5px] bg-chart-4" /> principle
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-[2.5px] bg-chart-1" /> pattern
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-[2.5px] bg-chart-2" /> decision
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-[2.5px] bg-chart-3" /> lesson
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-[2.5px] bg-chart-5" /> alert
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-[5px]">
          {activeNodes.map((n) => {
            const dotColor =
              n.freshness === "broken_ref" || (n.severity === "P0" && n.freshness === "drifted")
                ? "bg-chart-5"
                : n.freshness === "drifted"
                  ? "bg-chart-3"
                  : n.compat?.pattern === "code_exemplar"
                    ? "bg-chart-1"
                    : n.type === "principle"
                      ? "bg-chart-4"
                      : n.type === "decision"
                        ? "bg-chart-2"
                        : n.type === "lesson"
                          ? "bg-chart-3/85"
                          : "bg-muted-foreground/35"
            return (
              <Tooltip key={n.id}>
                <TooltipTrigger
                  render={
                    <button
                      type="button"
                      data-anim="dot"
                      onClick={() => onJumpToCard(n.id)}
                      aria-label={`${n.id} ${n.rule}`}
                      className={`size-2.5 cursor-pointer rounded-[3px] transition-transform duration-150 ease-[cubic-bezier(0.22,1,0.36,1)] hover:scale-[1.65] ${dotColor}`}
                    />
                  }
                />
                <TooltipContent side="top" className="max-w-xs flex-col items-start gap-1 py-2">
                  <div className="flex items-center gap-1.5 font-mono text-[11px] opacity-80">
                    <span>{n.id}</span>
                    <span>·</span>
                    <span>{n.severity}</span>
                    <span>·</span>
                    <span>{n.scope}</span>
                  </div>
                  <div className="line-clamp-2 text-xs leading-snug">{n.rule}</div>
                </TooltipContent>
              </Tooltip>
            )
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Left: Knowledge Composition & Scope Distribution */}
        <section className="surface-card flex flex-col gap-5 p-5">
          <div className="flex items-center gap-3">
            <span className="icon-tile size-9 text-chart-1">
              <IconLayersLinked className="size-4" />
            </span>
            <div>
              <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
                知识结构与健康度分布
              </h2>
              <p className="text-xs text-muted-foreground">
                悬停高亮细分构成，点击标签或作用域条直接下钻筛选
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-5">
            <SegmentedTickBar
              title="知识构成"
              subtitle="Type & Patterns"
              summaryPrimary={String(activeNodes.length)}
              summarySecondary="active"
              segments={typeSegments}
              ticks={52}
            />
            <Separator className="opacity-65" />
            <SegmentedTickBar
              title="源指针新鲜度"
              subtitle="Anti-Rot"
              summaryPrimary={h ? `${h.pointer_coverage_pct}%` : "-"}
              summarySecondary="covered"
              segments={freshnessSegments}
              ticks={52}
            />
            <Separator className="opacity-65" />
            <DistributionList
              title="Scope 作用域（点击筛选）"
              entries={scopeEntries}
              barClass="bg-chart-1"
              onClickKey={(sc) => onFilterCards({ scope: sc, special: "all" })}
            />
            <DistributionList
              title="Severity 优先级"
              entries={sevEntries}
              barClass="bg-chart-4"
            />
          </div>
        </section>

        {/* Right: L0 Wakeup Context Preview (Context Reading Card Style) */}
        <section className="surface-card flex flex-col gap-4 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="icon-tile size-9 text-chart-2">
                <IconBolt className="size-4" />
              </span>
              <div>
                <h2 className="flex items-center gap-2 text-[15px] font-semibold tracking-tight text-foreground">
                  L0 唤醒核预览
                  <Kbd>fos boot</Kbd>
                </h2>
                <p className="text-xs text-muted-foreground">
                  SessionStart 自动注入的极轻红线上下文（P1 代码范式不占此预算）
                </p>
              </div>
            </div>

            <div className="flex items-center gap-1.5">
              <Select
                value={bootProject}
                onValueChange={(val) => setBootProject(val ?? "__global__")}
              >
                <SelectTrigger size="sm" className="w-44 rounded-lg font-mono text-xs">
                  <SelectValue>
                    {(v: string | null) =>
                      !v || v === "__global__" ? "全局 (global/P0)" : `project: ${v}`
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__global__">全局 (global/P0)</SelectItem>
                  {projectScopes.map((s) => (
                    <SelectItem key={s} value={s}>
                      project: {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                type="button"
                variant="outline"
                size="icon-sm"
                className="rounded-lg"
                aria-label="复制 L0 唤醒核"
                disabled={!bootQuery.data?.kernel_text}
                onClick={copyBootKernel}
              >
                {copiedBoot ? (
                  <IconCheck className="size-3.5 text-chart-2" />
                ) : (
                  <IconCopy className="size-3.5" />
                )}
              </Button>
            </div>
          </div>

          {/* Signature Segmented Context Budget Card */}
          <div className="surface-inset p-4">
            <SegmentedTickBar
              title="L0 Context"
              subtitle={bootQuery.isLoading ? "loading" : "active"}
              summaryPrimary={`${bootUsed}t`}
              summarySecondary={`of ${bootBudget}t`}
              segments={bootSegments}
              capacity={bootBudget}
              threshold={Math.round(bootBudget * 0.85)}
              footnote={`预算上限 ${bootBudget}t · 当前注入 ${bootQuery.data?.count ?? 0} 条规则（85% 警戒线 ${Math.round(bootBudget * 0.85)}t）`}
              ticks={54}
            />
          </div>

          <div className="flex flex-1 flex-col justify-between gap-3.5">
            <div
              ref={bootBoxRef}
              className="surface-inset overflow-x-auto p-4 font-mono text-[13px] leading-6 whitespace-pre-wrap text-foreground"
            >
              {bootQuery.isLoading ? (
                <div className="flex flex-col gap-2">
                  <Skeleton className="h-3.5 w-2/3" />
                  <Skeleton className="h-3.5 w-full" />
                  <Skeleton className="h-3.5 w-5/6" />
                  <Skeleton className="h-3.5 w-3/4" />
                </div>
              ) : (
                bootQuery.data?.kernel_text || "(空)"
              )}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/70 pt-3.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-xs font-medium text-muted-foreground">快照演进</span>
                {dashboard.history.map((ver, idx) => (
                  <div key={ver} className="flex items-center gap-1">
                    {idx > 0 && <IconArrowRight className="size-3 text-muted-foreground/60" />}
                    <StatusPill
                      tone={ver === dashboard.snapshot_version ? "success" : "neutral"}
                      dot={ver === dashboard.snapshot_version}
                      mono
                    >
                      {ver}
                    </StatusPill>
                  </div>
                ))}
                {dashboard.rejected_snapshots.length > 0 && (
                  <StatusPill tone="danger" dot={false} mono>
                    门禁拦截: {dashboard.rejected_snapshots.join(", ")}
                  </StatusPill>
                )}
              </div>

              <Button
                variant="outline"
                size="sm"
                className="rounded-lg"
                disabled={dashboard.history.length < 2 || undoMutation.isPending}
                onClick={() => undoMutation.mutate()}
              >
                <IconArrowBackUp className="size-3.5" />
                回滚上一快照
              </Button>
            </div>
          </div>
        </section>
      </div>

      {/* PlugMem Utility & Guardrail Analytics */}
      {dashboard.utility && (
        <section className="surface-card flex flex-col gap-4 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="icon-tile size-9 text-chart-4">
                <IconSparkles className="size-4" />
              </span>
              <div>
                <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
                  召回效用闭环 &amp; 确定性守卫分析
                </h2>
                <p className="text-xs text-muted-foreground">
                  追踪真实命中、有用/噪声反馈与静态护栏硬拦截次数（点击条目跳转卡片）
                </p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusPill tone="neutral" dot={false} mono>
                命中 {dashboard.utility.total_recall_hits}
              </StatusPill>
              <StatusPill tone="success" mono>
                有用 {dashboard.utility.total_useful}
              </StatusPill>
              <StatusPill tone="neutral" dot={false} mono>
                噪声 {dashboard.utility.total_noise}
              </StatusPill>
              <StatusPill tone="purple" dot={false} mono>
                护栏拦截 {dashboard.utility.total_guard_blocks}
              </StatusPill>
              <StatusPill tone="info" dot={false} mono>
                <IconCode className="size-3" />
                范式 {codePatterns}
              </StatusPill>
              <StatusPill tone="neutral" dot={false} mono>
                守卫 {dashboard.hygiene?.guard_rules_count ?? 0} · 毕业{" "}
                {dashboard.hygiene?.graduated_nodes ?? 0}
              </StatusPill>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="flex flex-col gap-2">
              <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                高价值卡片 Top Useful
              </div>
              {dashboard.utility.top_useful.length === 0 ? (
                <div className="surface-inset p-4 text-xs text-muted-foreground">
                  暂无正反馈记录（可在 Cards 面板或 <Kbd>fos feedback</Kbd> 标记有用）
                </div>
              ) : (
                <div className="surface-inset divide-y divide-border/60 overflow-hidden">
                  {dashboard.utility.top_useful.map((item) => (
                    <div
                      key={item.id}
                      onClick={() => onJumpToCard(item.id)}
                      className="group flex cursor-pointer items-center gap-3 px-3.5 py-2.5 text-xs transition-colors duration-150 hover:bg-card/90"
                    >
                      <span className="icon-tile size-7 shrink-0 rounded-lg text-chart-2">
                        <IconThumbUp className="size-3.5" />
                      </span>
                      <span className="w-20 shrink-0 font-mono font-medium text-foreground">
                        {item.id}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-muted-foreground group-hover:text-foreground">
                        {item.rule}
                      </span>
                      <StatusPill tone="success" dot={false} mono>
                        +{item.useful} · {(item.utility_score * 100).toFixed(0)}%
                      </StatusPill>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex flex-col gap-2">
              <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                高频低效噪声候选 Low Utility ({dashboard.utility.low_utility_count})
              </div>
              {dashboard.utility.low_utility_nodes.length === 0 ? (
                <div className="surface-inset p-4 text-xs text-muted-foreground">
                  当前无低效噪声卡片
                </div>
              ) : (
                <div className="surface-inset divide-y divide-border/60 overflow-hidden">
                  {dashboard.utility.low_utility_nodes.map((item) => (
                    <div
                      key={item.id}
                      onClick={() => onJumpToCard(item.id)}
                      className="group flex cursor-pointer items-center gap-3 px-3.5 py-2.5 text-xs transition-colors duration-150 hover:bg-card/90"
                    >
                      <span className="icon-tile size-7 shrink-0 rounded-lg text-destructive">
                        <IconAlertTriangle className="size-3.5" />
                      </span>
                      <span className="w-20 shrink-0 font-mono font-medium text-foreground">
                        {item.id}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-muted-foreground group-hover:text-foreground">
                        {item.rule}
                      </span>
                      <StatusPill tone="danger" dot={false} mono>
                        噪声 {item.noise} · {(item.utility_score * 100).toFixed(0)}%
                      </StatusPill>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  )
}
