import { useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { IconArrowBackUp, IconArrowRight, IconBolt, IconCode } from "@tabler/icons-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { fosApi } from "@/lib/api"
import { useCountUp, useMatrixWave, usePanelTransition, useRevealGroup } from "@/lib/motion"
import type { DashboardResponse } from "@/lib/types"

function KpiValue({ value }: { value: number | string }) {
  const ref = useRef<HTMLDivElement | null>(null)
  useCountUp(ref, value)
  return (
    <div ref={ref} className="font-mono text-2xl font-semibold tracking-tight text-foreground">
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
    <div className="flex items-center gap-1" aria-hidden="true">
      {Array.from({ length: dots }).map((_, i) => (
        <span
          key={i}
          className={`size-1.5 rounded-full ${
            i < filled ? colorClass : "bg-muted-foreground/20"
          }`}
        />
      ))}
    </div>
  )
}

function DistributionBars({
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
    <div className="flex flex-col gap-1.5">
      <div className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      {entries.length === 0 ? (
        <div className="text-xs text-muted-foreground">暂无活跃数据</div>
      ) : (
        entries.map(([key, val]) => {
          const pct = Math.round((val / total) * 100)
          return (
            <div
              key={key}
              onClick={onClickKey ? () => onClickKey(key) : undefined}
              className={`flex items-center gap-2.5 rounded-sm px-1.5 py-1 text-xs transition-colors ${
                onClickKey ? "cursor-pointer hover:bg-accent/60" : ""
              }`}
            >
              <span className={`size-1.5 shrink-0 rounded-xs ${barClass}`} />
              <div className="min-w-0 flex-1 truncate font-mono text-foreground">{key}</div>
              <div className="w-9 text-right font-mono text-muted-foreground">{val}</div>
              <DotMeter ratio={val / max} colorClass={barClass} dots={10} />
              <div className="w-8 text-right font-mono text-[11px] text-muted-foreground">
                {pct}%
              </div>
            </div>
          )
        })
      )}
    </div>
  )
}

function SegmentedBar({
  title,
  segments,
}: {
  title: string
  segments: Array<{ label: string; count: number; colorClass: string }>
}) {
  const total = segments.reduce((acc, s) => acc + s.count, 0)
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-muted-foreground">{title}</span>
        <span className="font-mono text-muted-foreground">{total}</span>
      </div>
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted">
        {total > 0 &&
          segments
            .filter((s) => s.count > 0)
            .map((s) => (
              <div
                key={s.label}
                className={`h-full ${s.colorClass}`}
                style={{ width: `${(s.count / total) * 100}%` }}
                title={`${s.label}: ${s.count}`}
              />
            ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {segments
          .filter((s) => s.count > 0)
          .map((s) => (
            <span key={s.label} className="inline-flex items-center gap-1.5 font-mono">
              <span className={`size-2 rounded-full ${s.colorClass}`} />
              {s.label} {s.count}
            </span>
          ))}
      </div>
    </div>
  )
}

export function OverviewPanel({ dashboard, onJumpToCard, onFilterCards }: OverviewPanelProps) {
  const [bootProject, setBootProject] = useState<string>("__global__")
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

  const freshnessSegments = useMemo(() => {
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
      { label: "fresh", count: fresh, colorClass: "bg-chart-2" },
      { label: "drifted", count: drifted, colorClass: "bg-chart-3" },
      { label: "broken_ref", count: broken, colorClass: "bg-chart-5" },
      { label: "untracked", count: untracked, colorClass: "bg-muted-foreground/40" },
    ]
  }, [activeNodes])

  const typeSegments = useMemo(() => {
    const patterns = activeNodes.filter((n) => n.compat?.pattern === "code_exemplar").length
    const byType = dashboard.distributions.by_type
    const nonPatternDecisions = Math.max(0, (byType.decision ?? 0) - patterns)
    return [
      { label: "principle", count: byType.principle ?? 0, colorClass: "bg-chart-4" },
      { label: "pattern", count: patterns, colorClass: "bg-chart-1" },
      { label: "decision", count: nonPatternDecisions, colorClass: "bg-chart-2" },
      { label: "lesson", count: byType.lesson ?? 0, colorClass: "bg-chart-3" },
      { label: "thought/other", count: (byType.thought ?? 0) + (byType.outcome ?? 0), colorClass: "bg-muted-foreground/40" },
    ]
  }, [activeNodes, dashboard.distributions.by_type])

  const c = dashboard.counts
  const h = dashboard.hygiene
  const codePatterns = h?.code_patterns_count ?? activeNodes.filter((n) => n.compat?.pattern === "code_exemplar").length
  const alertsCount = (h?.drifted_nodes ?? 0) + (h?.broken_ref_nodes ?? 0)

  const kpis = [
    {
      label: "活跃记忆 Active",
      value: c.active_nodes,
      sub: `总节点 ${c.total_nodes} · L1 均长 ${h?.avg_rule_tokens ?? "-"}t`,
      onClick: () => onFilterCards({ scope: "__all__", special: "all" }),
    },
    {
      label: "代码范式 Patterns",
      value: codePatterns,
      sub: "符号锚定 + SemVer 包络",
      onClick: () => onFilterCards({ scope: "__all__", special: "patterns" }),
    },
    {
      label: "原文指针覆盖",
      value: h ? `${h.pointer_coverage_pct}%` : "-",
      sub: `source_ref ${h?.pointer_nodes ?? 0} · 超胖 ${h?.bloated_nodes ?? 0}`,
      onClick: undefined,
    },
    {
      label: "抗腐化告警",
      value: alertsCount,
      sub: `漂移 ${h?.drifted_nodes ?? 0} · 断链 ${h?.broken_ref_nodes ?? 0}`,
      onClick: alertsCount > 0 ? () => onFilterCards({ scope: "__all__", special: "issues" }) : undefined,
    },
    {
      label: "不可变账本 Atoms",
      value: c.ledger_atoms,
      sub: `纠偏 ${c.superseded_nodes} · 归档 ${c.archived_nodes}`,
      onClick: undefined,
    },
    {
      label: "待审推荐 Inbox",
      value: c.pending_proposals ?? 0,
      sub: `关系边 ${c.edges} · 金标 ${c.golden_cases}`,
      onClick: undefined,
    },
  ]

  const scopeEntries = Object.entries(dashboard.distributions.by_scope).sort((a, b) => b[1] - a[1])
  const sevEntries = Object.entries(dashboard.distributions.by_severity)
  const projectScopes = dashboard.scopes.filter((s) => s !== "global")

  const bootUsed = bootQuery.data?.used_tokens ?? 0
  const bootBudget = bootQuery.data?.budget_tokens ?? 300
  const bootPct = Math.min(100, Math.round((bootUsed / Math.max(1, bootBudget)) * 100))

  useRevealGroup(rootRef, "[data-anim='kpi']", [dashboard.snapshot_version], { y: 6, duration: 0.26 })
  useMatrixWave(matrixRef, "[data-anim='dot']", [dashboard.snapshot_version, activeNodes.length])
  usePanelTransition(bootBoxRef, [bootProject, bootQuery.data?.kernel_text], { y: 4, duration: 0.2 })

  return (
    <div ref={rootRef} className="flex flex-col gap-4">
      {/* Hairline KPI telemetry strip */}
      <div className="grid grid-cols-2 border-y border-border divide-y divide-border sm:grid-cols-3 lg:grid-cols-6 lg:divide-y-0 lg:divide-x">
        {kpis.map((item) => (
          <div
            key={item.label}
            data-anim="kpi"
            onClick={item.onClick}
            className={`flex flex-col justify-between gap-1 px-3.5 py-2.5 transition-colors ${
              item.onClick ? "cursor-pointer hover:bg-accent/40" : ""
            }`}
          >
            <div className="text-xs font-medium text-muted-foreground">{item.label}</div>
            <KpiValue value={item.value} />
            <div className="truncate text-xs text-muted-foreground">{item.sub}</div>
          </div>
        ))}
      </div>

      {/* Signature TUI dot-matrix memory map */}
      <div ref={matrixRef} className="border-b border-border pb-3.5">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 font-mono text-[11px]">
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">MEMORY MATRIX</span>
            <span className="text-muted-foreground">
              {activeNodes.length} active · {codePatterns} patterns · {alertsCount} alerts
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <span className="size-1.5 rounded-[1px] bg-chart-4" /> principle
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="size-1.5 rounded-[1px] bg-chart-1" /> pattern
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="size-1.5 rounded-[1px] bg-chart-2" /> decision
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="size-1.5 rounded-[1px] bg-chart-3" /> lesson
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="size-1.5 rounded-[1px] bg-chart-5" /> alert
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-1">
          {activeNodes.map((n) => {
            const dotColor =
              n.freshness === "broken_ref" || n.severity === "P0" && n.freshness === "drifted"
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
                          : "bg-muted-foreground/45"
            return (
              <Tooltip key={n.id}>
                <TooltipTrigger
                  render={
                    <button
                      type="button"
                      data-anim="dot"
                      onClick={() => onJumpToCard(n.id)}
                      aria-label={`${n.id} ${n.rule}`}
                      className={`size-2 cursor-pointer rounded-[2px] transition-transform hover:scale-150 ${dotColor}`}
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

      <div className="grid grid-cols-1 gap-6 border-b border-border pb-4 lg:grid-cols-2 lg:divide-x lg:divide-border">
        <section className="flex flex-col gap-3.5">
          <div>
            <h2 className="text-sm font-medium text-foreground">知识结构与健康度分布</h2>
            <p className="text-xs text-muted-foreground">点击任意作用域条可直接下钻到 Cards 面板筛选</p>
          </div>
          <div className="flex flex-col gap-3.5">
            <SegmentedBar title="知识构成 (Type & Patterns)" segments={typeSegments} />
            <Separator />
            <SegmentedBar title="源指针新鲜度 (Anti-Rot Freshness)" segments={freshnessSegments} />
            <Separator />
            <DistributionBars
              title="Scope 作用域（点击筛选）"
              entries={scopeEntries}
              barClass="bg-chart-1"
              onClickKey={(sc) => onFilterCards({ scope: sc, special: "all" })}
            />
            <Separator />
            <DistributionBars title="Severity 优先级" entries={sevEntries} barClass="bg-chart-4" />
          </div>
        </section>

        <section className="flex flex-col gap-3 lg:pl-6">
          <div className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <IconBolt className="size-4 text-chart-2" />
                <h2 className="flex items-center gap-2 text-sm font-medium text-foreground">
                  L0 唤醒核预览 <Kbd>fos boot</Kbd>
                </h2>
              </div>
              <div className="flex items-center gap-2">
                <Select
                  value={bootProject}
                  onValueChange={(val) => setBootProject(val ?? "__global__")}
                >
                  <SelectTrigger size="sm" className="w-40 font-mono text-xs">
                    <SelectValue placeholder="选择作用域" />
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
                <Badge variant="outline" className="rounded-sm font-mono text-xs">
                  {bootQuery.data ? `${bootUsed} / ${bootBudget}t` : "..."}
                </Badge>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              SessionStart 自动注入给 AI Agent 的极轻红线上下文（P1 代码范式不占此预算）
            </p>
            {/* Visual L0 token budget bar */}
            <div className="mt-1 flex items-center gap-2.5">
              <Progress
                value={bootPct}
                className={
                  bootPct >= 95
                    ? "flex-1 [&_[data-slot=progress-indicator]]:bg-chart-3"
                    : "flex-1 [&_[data-slot=progress-indicator]]:bg-chart-2"
                }
              />
              <span className="font-mono text-xs text-muted-foreground">{bootPct}%</span>
            </div>
          </div>

          <div className="flex flex-1 flex-col justify-between gap-3">
            <div
              ref={bootBoxRef}
              className="overflow-x-auto rounded-sm border border-border bg-card/60 p-3.5 font-mono text-[13px] leading-6 whitespace-pre-wrap text-foreground"
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

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="secondary" className="rounded-sm text-xs">
                  快照演进
                </Badge>
                {dashboard.history.map((ver, idx) => (
                  <div key={ver} className="flex items-center gap-1">
                    {idx > 0 && <IconArrowRight className="size-3 text-muted-foreground" />}
                    <Badge
                      variant={ver === dashboard.snapshot_version ? "default" : "outline"}
                      className="rounded-sm font-mono text-xs"
                    >
                      {ver}
                    </Badge>
                  </div>
                ))}
                {dashboard.rejected_snapshots.length > 0 && (
                  <Badge variant="destructive" className="rounded-sm font-mono text-xs">
                    门禁拦截: {dashboard.rejected_snapshots.join(", ")}
                  </Badge>
                )}
              </div>

              <Button
                variant="outline"
                size="sm"
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
        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-medium text-foreground">
                召回效用闭环 &amp; 确定性守卫分析 (PlugMem / Harness)
              </h2>
              <p className="text-xs text-muted-foreground">
                追踪每次召回的真实命中、有用/噪声反馈与静态护栏硬拦截次数（点击条目跳转卡片）
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="outline" className="font-mono text-xs">
                命中 {dashboard.utility.total_recall_hits}
              </Badge>
              <Badge variant="secondary" className="font-mono text-xs">
                有用 {dashboard.utility.total_useful}
              </Badge>
              <Badge variant="outline" className="font-mono text-xs">
                噪声 {dashboard.utility.total_noise}
              </Badge>
              <Badge variant="outline" className="font-mono text-xs">
                护栏拦截 {dashboard.utility.total_guard_blocks}
              </Badge>
              <Badge variant="outline" className="font-mono text-xs">
                <IconCode className="mr-1 size-3" />
                代码范式 {codePatterns}
              </Badge>
              <Badge variant="outline" className="font-mono text-xs">
                静态规则 {dashboard.hygiene?.guard_rules_count ?? 0} · 毕业{" "}
                {dashboard.hygiene?.graduated_nodes ?? 0}
              </Badge>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="flex flex-col gap-2">
              <div className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
                高价值卡片 Top Useful
              </div>
              {dashboard.utility.top_useful.length === 0 ? (
                <div className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                  暂无正反馈记录（可在 Cards 面板或 <Kbd>fos feedback</Kbd> 标记有用）
                </div>
              ) : (
                <div className="rounded-md border border-border">
                  <Table>
                    <TableBody>
                      {dashboard.utility.top_useful.map((item) => (
                        <TableRow
                          key={item.id}
                          onClick={() => onJumpToCard(item.id)}
                          className="cursor-pointer text-xs"
                        >
                          <TableCell className="w-24 font-mono text-foreground">
                            {item.id}
                          </TableCell>
                          <TableCell className="max-w-0 truncate text-muted-foreground">
                            {item.rule}
                          </TableCell>
                          <TableCell className="w-24 text-right">
                            <Badge variant="secondary" className="font-mono text-[11px] text-chart-2">
                              +{item.useful} · {(item.utility_score * 100).toFixed(0)}%
                            </Badge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <div className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
                高频低效噪声候选 Low Utility ({dashboard.utility.low_utility_count})
              </div>
              {dashboard.utility.low_utility_nodes.length === 0 ? (
                <div className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                  当前无低效噪声卡片
                </div>
              ) : (
                <div className="rounded-md border border-border">
                  <Table>
                    <TableBody>
                      {dashboard.utility.low_utility_nodes.map((item) => (
                        <TableRow
                          key={item.id}
                          onClick={() => onJumpToCard(item.id)}
                          className="cursor-pointer text-xs"
                        >
                          <TableCell className="w-24 font-mono text-foreground">
                            {item.id}
                          </TableCell>
                          <TableCell className="max-w-0 truncate text-muted-foreground">
                            {item.rule}
                          </TableCell>
                          <TableCell className="w-28 text-right">
                            <Badge variant="destructive" className="font-mono text-[11px]">
                              噪声 {item.noise} · {(item.utility_score * 100).toFixed(0)}%
                            </Badge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  )
}
