import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { IconArrowBackUp, IconArrowRight, IconBolt } from "@tabler/icons-react"
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
}

function DistributionBars({
  title,
  entries,
  barClass,
}: {
  title: string
  entries: Array<[string, number]>
  barClass: string
}) {
  const max = Math.max(1, ...entries.map(([, v]) => v))
  return (
    <div className="flex flex-col gap-2.5">
      <div className="text-xs font-medium text-muted-foreground">{title}</div>
      {entries.length === 0 ? (
        <div className="text-xs text-muted-foreground">暂无活跃数据</div>
      ) : (
        entries.map(([key, val]) => (
          <div key={key} className="flex items-center gap-3 text-xs">
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

export function OverviewPanel({ dashboard }: OverviewPanelProps) {
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

  const c = dashboard.counts
  const kpis = [
    { label: "活跃记忆 Active", value: c.active_nodes, sub: `总节点 ${c.total_nodes}` },
    { label: "已纠偏 Superseded", value: c.superseded_nodes, sub: "双时态保留可回溯" },
    { label: "已归档 Archived", value: c.archived_nodes, sub: "过期或已守卫化" },
    { label: "不可变账本 Atoms", value: c.ledger_atoms, sub: "Append-Only JSONL" },
    { label: "关系图谱 Edges", value: c.edges, sub: "SUPERSEDES / APPLIES_TO" },
    { label: "黄金回归集 Golden", value: c.golden_cases, sub: `已钉选 P0: ${c.pinned_nodes}` },
  ]

  const scopeEntries = Object.entries(dashboard.distributions.by_scope).sort((a, b) => b[1] - a[1])
  const sevEntries = Object.entries(dashboard.distributions.by_severity)
  const projectScopes = dashboard.scopes.filter((s) => s !== "global")

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {kpis.map((item) => (
          <Card key={item.label} className="gap-2 py-4">
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
            <CardTitle className="text-sm font-medium">分布概览</CardTitle>
            <CardDescription>按作用域与优先级统计当前活跃记忆节点</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-5">
            <DistributionBars title="Scope 作用域" entries={scopeEntries} barClass="bg-chart-1" />
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
                  {bootQuery.data
                    ? `${bootQuery.data.used_tokens} / ${bootQuery.data.budget_tokens}t`
                    : "..."}
                </Badge>
              </div>
            </div>
            <CardDescription>SessionStart 自动注入给 AI Agent 的极轻红线上下文</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col justify-between gap-4">
            <div className="overflow-x-auto rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-foreground">
              {bootQuery.isLoading
                ? "加载唤醒核..."
                : bootQuery.data?.kernel_text || "(空)"}
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
    </div>
  )
}
