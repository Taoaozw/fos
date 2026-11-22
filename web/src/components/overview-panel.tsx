import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { IconArrowBackUp, IconArrowRight, IconBolt, IconCode } from "@tabler/icons-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { fosApi } from "@/lib/api"
import type { DashboardResponse } from "@/lib/types"

interface OverviewPanelProps {
  dashboard: DashboardResponse
  onJumpToCard: (nodeId: string) => void
  onFilterCards: (opts: { scope?: string; special?: "all" | "patterns" | "issues" | "guards" }) => void
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
  return (
    <div className="flex flex-col gap-2">
      <div className="text-xs font-medium text-muted-foreground">{title}</div>
      {entries.length === 0 ? (
        <div className="text-xs text-muted-foreground">暂无活跃数据</div>
      ) : (
        entries.map(([key, val]) => (
          <div
            key={key}
            onClick={onClickKey ? () => onClickKey(key) : undefined}
            className={`flex items-center gap-3 rounded-md px-1.5 py-1 text-xs transition-colors ${
              onClickKey ? "cursor-pointer hover:bg-muted/50" : ""
            }`}
          >
            <div className="w-28 truncate font-mono text-foreground">{key}</div>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className={`h-full rounded-full ${barClass}`}
                style={{ width: `${Math.round((val / max) * 100)}%` }}
              />
            </div>
            <div className="w-8 text-right font-mono text-muted-foreground">{val}</div>
          </div>
        ))
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

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {kpis.map((item) => (
          <Card
            key={item.label}
            onClick={item.onClick}
            className={`gap-2 py-4 transition-colors ${
              item.onClick ? "cursor-pointer hover:border-foreground/40" : ""
            }`}
          >
            <CardHeader className="px-4 py-0">
              <CardDescription className="text-xs">{item.label}</CardDescription>
            </CardHeader>
            <CardContent className="px-4 py-0">
              <div className="font-mono text-2xl font-medium text-foreground">{item.value}</div>
              <div className="mt-1 text-xs text-muted-foreground">{item.sub}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">知识结构与健康度分布</CardTitle>
            <CardDescription>点击任意作用域条可直接下钻到 Cards 面板筛选</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
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
          </CardContent>
        </Card>

        <Card className="flex flex-col">
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <IconBolt className="size-4 text-muted-foreground" />
                <CardTitle className="text-sm font-medium">L0 唤醒核预览 (fos boot)</CardTitle>
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
                <Badge variant="outline" className="rounded-md font-mono text-xs">
                  {bootQuery.data ? `${bootUsed} / ${bootBudget}t` : "..."}
                </Badge>
              </div>
            </div>
            <CardDescription>SessionStart 自动注入给 AI Agent 的极轻红线上下文（P1 代码范式不占此预算）</CardDescription>
            {/* Visual L0 token budget bar */}
            <div className="mt-2 flex items-center gap-2">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                <div
                  className={`h-full rounded-full transition-all ${
                    bootPct >= 95 ? "bg-chart-3" : "bg-chart-2"
                  }`}
                  style={{ width: `${bootPct}%` }}
                />
              </div>
              <span className="font-mono text-xs text-muted-foreground">{bootPct}%</span>
            </div>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col justify-between gap-4">
            <div className="overflow-x-auto rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-foreground">
              {bootQuery.isLoading ? "加载唤醒核..." : bootQuery.data?.kernel_text || "(空)"}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="secondary" className="rounded-md text-xs">
                  快照演进
                </Badge>
                {dashboard.history.map((ver, idx) => (
                  <div key={ver} className="flex items-center gap-1">
                    {idx > 0 && <IconArrowRight className="size-3 text-muted-foreground" />}
                    <Badge
                      variant={ver === dashboard.snapshot_version ? "default" : "outline"}
                      className="rounded-md font-mono text-xs"
                    >
                      {ver}
                    </Badge>
                  </div>
                ))}
                {dashboard.rejected_snapshots.length > 0 && (
                  <Badge variant="destructive" className="rounded-md font-mono text-xs">
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
          </CardContent>
        </Card>
      </div>

      {/* PlugMem Utility & Guardrail Analytics */}
      {dashboard.utility && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle className="text-sm font-medium">
                  召回效用闭环 &amp; 确定性守卫分析 (PlugMem / Harness)
                </CardTitle>
                <CardDescription>
                  追踪每次召回的真实命中、有用/噪声反馈与静态护栏硬拦截次数（点击条目跳转卡片）
                </CardDescription>
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
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="flex flex-col gap-2">
              <div className="text-xs font-medium text-muted-foreground">
                高价值卡片 Top Useful
              </div>
              {dashboard.utility.top_useful.length === 0 ? (
                <div className="text-xs text-muted-foreground">
                  暂无正反馈记录（可在 Cards 面板或 `fos feedback` 标记有用）
                </div>
              ) : (
                dashboard.utility.top_useful.map((item) => (
                  <div
                    key={item.id}
                    onClick={() => onJumpToCard(item.id)}
                    className="flex cursor-pointer items-center justify-between gap-2 rounded-md border border-border bg-muted/30 px-2.5 py-1.5 text-xs transition-colors hover:border-foreground/40"
                  >
                    <div className="min-w-0 flex-1 truncate">
                      <span className="mr-1.5 font-mono text-foreground">{item.id}</span>
                      <span className="text-muted-foreground">{item.rule}</span>
                    </div>
                    <Badge variant="secondary" className="shrink-0 font-mono text-xs">
                      +{item.useful} · {(item.utility_score * 100).toFixed(0)}%
                    </Badge>
                  </div>
                ))
              )}
            </div>
            <div className="flex flex-col gap-2">
              <div className="text-xs font-medium text-muted-foreground">
                高频低效噪声候选 Low Utility ({dashboard.utility.low_utility_count})
              </div>
              {dashboard.utility.low_utility_nodes.length === 0 ? (
                <div className="text-xs text-muted-foreground">当前无低效噪声卡片</div>
              ) : (
                dashboard.utility.low_utility_nodes.map((item) => (
                  <div
                    key={item.id}
                    onClick={() => onJumpToCard(item.id)}
                    className="flex cursor-pointer items-center justify-between gap-2 rounded-md border border-border bg-muted/30 px-2.5 py-1.5 text-xs transition-colors hover:border-foreground/40"
                  >
                    <div className="min-w-0 flex-1 truncate">
                      <span className="mr-1.5 font-mono text-foreground">{item.id}</span>
                      <span className="text-muted-foreground">{item.rule}</span>
                    </div>
                    <Badge variant="destructive" className="shrink-0 font-mono text-xs">
                      噪声 {item.noise} · {(item.utility_score * 100).toFixed(0)}%
                    </Badge>
                  </div>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
