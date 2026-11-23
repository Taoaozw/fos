import { useRef, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { IconMoonStars, IconShieldCheck } from "@tabler/icons-react"
import { toast } from "sonner"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { fosApi } from "@/lib/api"
import { useRevealGroup } from "@/lib/motion"
import type { DashboardResponse, EvalResponse } from "@/lib/types"

interface AuditPanelProps {
  dashboard: DashboardResponse
}

export function AuditPanel({ dashboard }: AuditPanelProps) {
  const [evalReport, setEvalReport] = useState<EvalResponse | null>(null)
  const queryClient = useQueryClient()
  const gatesRef = useRef<HTMLDivElement | null>(null)
  const ledgerRef = useRef<HTMLDivElement | null>(null)

  useRevealGroup(gatesRef, "[data-anim='gate']", [evalReport?.snapshot_version, evalReport?.passed], {
    y: 6,
    duration: 0.24,
  })
  useRevealGroup(ledgerRef, "tbody tr", [dashboard.recent_atoms.length], {
    y: 4,
    duration: 0.22,
    maxItems: 20,
  })

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
      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-medium text-foreground">四维回归门禁与睡眠编译</h2>
            <p className="text-xs text-muted-foreground">
              Recall / Temporal / Fidelity / Behavior 自动守门，未通过则拦截上线并保留原快照
            </p>
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
        <div>
          {!evalReport ? (
            <Alert>
              <IconShieldCheck className="size-4 text-muted-foreground" />
              <AlertDescription className="text-xs text-muted-foreground">
                点击「运行四维门禁」实时验证当前快照 <Kbd>{dashboard.snapshot_version}</Kbd> 的四维回归指标。
              </AlertDescription>
            </Alert>
          ) : (
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge
                  variant={evalReport.passed ? "default" : "destructive"}
                  className="rounded-sm font-mono text-xs"
                >
                  {evalReport.passed ? "ALL GATES PASSED" : "GATE FAILED"}
                </Badge>
                <Badge variant="outline" className="rounded-sm font-mono text-xs">
                  snap: {evalReport.snapshot_version}
                </Badge>
                <Badge variant="outline" className="rounded-sm font-mono text-xs">
                  golden: {evalReport.golden_cases}
                </Badge>
              </div>
              <div ref={gatesRef} className="grid grid-cols-1 border-y border-border divide-y divide-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-y-0 lg:divide-x">
                {Object.entries(evalReport.gates).map(([name, metrics]) => (
                  <div
                    key={name}
                    data-anim="gate"
                    className="flex flex-col gap-1.5 px-3 py-2.5"
                  >
                    <div className="flex items-center justify-between">
                      <div className="font-mono text-xs font-medium uppercase text-muted-foreground">
                        {name}
                      </div>
                      <Badge
                        variant={metrics.passed ? "outline" : "destructive"}
                        className="rounded-sm font-mono text-xs"
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
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-medium text-foreground">
            不可变原始账本时间线 (ledger/events-YYYY-MM.jsonl)
          </h2>
          <p className="font-mono text-xs text-muted-foreground">
            Append-Only 唯一真相源 · 最近 {dashboard.recent_atoms.length} / {dashboard.counts.ledger_atoms} 条
          </p>
        </div>
        <div ref={ledgerRef}>
        <ScrollArea className="h-[calc(100vh-290px)] min-h-[420px] border-y border-border">
            <Table className="table-fixed">
              <TableHeader>
                <TableRow className="font-mono text-[11px] uppercase">
                  <TableHead className="w-36">ID / Kind</TableHead>
                  <TableHead className="w-40">Scope / Source</TableHead>
                  <TableHead>Raw Content</TableHead>
                  <TableHead className="w-44 pr-4 text-right">Timestamp</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {dashboard.recent_atoms.map((atom) => {
                  const redacted = Array.isArray(atom.meta?.redacted_types)
                    ? (atom.meta.redacted_types as string[])
                    : []
                  const evRef =
                    typeof atom.meta?.evidence_ref === "string" ? atom.meta.evidence_ref : ""
                  const compactTs = atom.ts.replace("T", " ").replace(/\.\d+/, "").slice(0, 19)
                  return (
                    <TableRow key={atom.id} className="align-top text-xs">
                      <TableCell className="space-y-1">
                        <div className="truncate font-mono text-foreground" title={atom.id}>
                          {atom.id}
                        </div>
                        <div className="flex flex-wrap items-center gap-1">
                          <Badge variant="secondary" className="rounded-sm font-mono text-[10px]">
                            {atom.kind}
                          </Badge>
                          {redacted.length > 0 && (
                            <Badge variant="destructive" className="rounded-sm font-mono text-[10px]">
                              脱敏: {redacted.join(",")}
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="space-y-1 font-mono text-[11px]">
                        <div className="truncate text-foreground" title={atom.scope}>
                          {atom.scope}
                        </div>
                        <div className="truncate text-muted-foreground" title={atom.source}>
                          {atom.source}
                        </div>
                        {evRef && (
                          <div className="truncate text-muted-foreground" title={evRef}>
                            {evRef}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <div className="max-h-28 overflow-y-auto overflow-x-hidden break-words rounded-sm border border-border/70 bg-card/50 px-3 py-2 font-mono text-[13px] leading-6 whitespace-pre-wrap text-foreground">
                          {atom.raw}
                        </div>
                      </TableCell>
                      <TableCell className="pr-4 text-right font-mono text-[11px] text-muted-foreground">
                        <div title={atom.ts}>{compactTs}</div>
                        <div className="truncate" title={atom.device}>
                          {atom.device}
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </ScrollArea>
        </div>
      </section>
    </div>
  )
}
