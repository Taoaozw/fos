import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { IconMoonStars, IconShieldCheck } from "@tabler/icons-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { fosApi } from "@/lib/api"
import type { DashboardResponse, EvalResponse } from "@/lib/types"

interface AuditPanelProps {
  dashboard: DashboardResponse
}

export function AuditPanel({ dashboard }: AuditPanelProps) {
  const [evalReport, setEvalReport] = useState<EvalResponse | null>(null)
  const queryClient = useQueryClient()

  const evalMutation = useMutation({
    mutationFn: () => fosApi.runEval("all"),
    onSuccess: (res) => {
      setEvalReport(res)
      if (res.passed) {
        toast.success(`四维回归门禁全部通过 (${res.snapshot_version})`)
      } else {
        toast.error(`回归门禁发现退化：${res.regressions.join("; ")}`)
      }
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const dreamMutation = useMutation({
    mutationFn: () => fosApi.runDream(false),
    onSuccess: (res) => {
      if (res.gate) setEvalReport(res.gate)
      toast.success(`睡眠编译已晋升快照：${res.baseline_version} → ${res.current_version}`)
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(`睡眠编译拦截/失败：${err.message}`),
  })

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <CardTitle className="text-sm font-medium">四维回归门禁与睡眠编译</CardTitle>
              <CardDescription>
                Recall / Temporal / Fidelity / Behavior 自动守门，未通过则拦截上线并保留原快照
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={evalMutation.isPending}
                onClick={() => evalMutation.mutate()}
              >
                <IconShieldCheck className="size-4" />
                {evalMutation.isPending ? "评测中..." : "运行四维门禁"}
              </Button>
              <Button
                size="sm"
                disabled={dreamMutation.isPending}
                onClick={() => dreamMutation.mutate()}
              >
                <IconMoonStars className="size-4" />
                {dreamMutation.isPending ? "编译中..." : "触发睡眠编译 (Dream)"}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {!evalReport ? (
            <div className="text-xs text-muted-foreground">
              点击「运行四维门禁」实时验证当前快照 {dashboard.snapshot_version} 的四维回归指标。
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge
                  variant={evalReport.passed ? "default" : "destructive"}
                  className="rounded-md font-mono text-xs"
                >
                  {evalReport.passed ? "ALL GATES PASSED" : "GATE FAILED"}
                </Badge>
                <Badge variant="outline" className="rounded-md font-mono text-xs">
                  snap: {evalReport.snapshot_version}
                </Badge>
                <Badge variant="outline" className="rounded-md font-mono text-xs">
                  golden: {evalReport.golden_cases}
                </Badge>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {Object.entries(evalReport.gates).map(([name, metrics]) => (
                  <div
                    key={name}
                    className="flex flex-col gap-1.5 rounded-lg border border-border bg-muted/30 p-3"
                  >
                    <div className="flex items-center justify-between">
                      <div className="font-mono text-xs font-medium uppercase text-muted-foreground">
                        {name}
                      </div>
                      <Badge
                        variant={metrics.passed ? "outline" : "destructive"}
                        className="rounded-md font-mono text-xs"
                      >
                        {metrics.passed ? "PASS" : "FAIL"}
                      </Badge>
                    </div>
                    <div className="font-mono text-xs text-foreground">
                      {name === "recall" &&
                        `Recall@5=${metrics.recall_at_5} · MRR=${metrics.mrr} (${metrics.cases} cases)`}
                      {name === "temporal" &&
                        `Leaks=${metrics.stale_leaks} · AsOf=${metrics.as_of_restore_rate}`}
                      {name === "fidelity" &&
                        `Retention=${metrics.retention_rate} (${metrics.checked_nodes} nodes)`}
                      {name === "behavior" &&
                        `GuardHit=${metrics.guard_hit_rate} (${metrics.cases} cases)`}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">
            不可变原始账本时间线 (ledger/events-YYYY-MM.jsonl)
          </CardTitle>
          <CardDescription>
            Append-Only 唯一真相源（展示最近 {dashboard.recent_atoms.length} 条，共{" "}
            {dashboard.counts.ledger_atoms} 条）
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ScrollArea className="h-[500px] pr-3">
            <div className="flex flex-col gap-3">
              {dashboard.recent_atoms.map((atom) => {
                const redacted = Array.isArray(atom.meta?.redacted_types)
                  ? (atom.meta.redacted_types as string[])
                  : []
                const evRef =
                  typeof atom.meta?.evidence_ref === "string" ? atom.meta.evidence_ref : ""
                return (
                  <div
                    key={atom.id}
                    className="flex flex-col gap-2 rounded-lg border border-border bg-muted/20 p-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant="outline" className="rounded-md font-mono text-xs">
                          {atom.id}
                        </Badge>
                        <Badge variant="secondary" className="rounded-md text-xs">
                          {atom.kind}
                        </Badge>
                        <Badge variant="outline" className="rounded-md text-xs">
                          {atom.scope}
                        </Badge>
                        <Badge variant="outline" className="rounded-md font-mono text-xs">
                          {atom.source}
                        </Badge>
                        {evRef && (
                          <Badge variant="outline" className="rounded-md font-mono text-xs">
                            {evRef}
                          </Badge>
                        )}
                        {redacted.length > 0 && (
                          <Badge variant="destructive" className="rounded-md text-xs">
                            已脱敏: {redacted.join(", ")}
                          </Badge>
                        )}
                      </div>
                      <div className="font-mono text-xs text-muted-foreground">
                        {atom.ts} · {atom.device}
                      </div>
                    </div>
                    <div className="max-h-28 overflow-auto rounded-md border border-border bg-background p-2.5 font-mono text-xs whitespace-pre-wrap text-foreground">
                      {atom.raw}
                    </div>
                  </div>
                )
              })}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  )
}
