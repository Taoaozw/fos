import { useMemo, useRef, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { debounce, useQueryState } from "nuqs"
import {
  IconChevronDown,
  IconChevronUp,
  IconMoonStars,
  IconSearch,
  IconShieldCheck,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { StatusPill } from "@/components/shared/status-pill"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Kbd } from "@/components/ui/kbd"
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
import { auditSearchParser } from "@/lib/url-state"
import type { DashboardResponse, EvalResponse } from "@/lib/types"

interface AuditPanelProps {
  dashboard: DashboardResponse
}

export function AuditPanel({ dashboard }: AuditPanelProps) {
  const [evalReport, setEvalReport] = useState<EvalResponse | null>(null)
  const [ledgerSearch, setLedgerSearch] = useQueryState("aq", auditSearchParser)
  const [expandedAtomIds, setExpandedAtomIds] = useState<Set<string>>(() => new Set())
  const queryClient = useQueryClient()
  const gatesRef = useRef<HTMLDivElement | null>(null)
  const ledgerRef = useRef<HTMLDivElement | null>(null)

  const filteredAtoms = useMemo(() => {
    const q = ledgerSearch.trim().toLowerCase()
    if (!q) return dashboard.recent_atoms
    return dashboard.recent_atoms.filter((atom) =>
      `${atom.id} ${atom.kind} ${atom.scope} ${atom.source} ${atom.raw}`
        .toLowerCase()
        .includes(q)
    )
  }, [dashboard.recent_atoms, ledgerSearch])

  useRevealGroup(gatesRef, "[data-anim='gate']", [evalReport?.snapshot_version, evalReport?.passed], {
    y: 6,
    duration: 0.24,
  })
  useRevealGroup(ledgerRef, "tbody tr", [filteredAtoms.length], {
    y: 4,
    duration: 0.2,
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

  const toggleAtomExpand = (id: string) => {
    setExpandedAtomIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Regression Gate & Dream Compiler */}
      <section className="surface-card flex flex-col gap-4 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="icon-tile size-9 text-chart-2">
              <IconShieldCheck className="size-4" />
            </span>
            <div>
              <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
                四维回归门禁与睡眠编译
              </h2>
              <p className="text-xs text-muted-foreground">
                Recall / Temporal / Fidelity / Behavior 自动守门，未通过则拦截上线并保留原快照
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="rounded-lg"
              disabled={evalMutation.isPending}
              onClick={() => evalMutation.mutate()}
            >
              <IconShieldCheck className="size-4" />
              {evalMutation.isPending ? "评测中..." : "运行四维门禁"}
            </Button>
            <Button
              size="sm"
              className="rounded-lg shadow-2xs"
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
            <Alert className="surface-inset border-border/65">
              <IconShieldCheck className="size-4 text-muted-foreground" />
              <AlertDescription className="text-xs text-muted-foreground">
                点击「运行四维门禁」实时验证当前快照 <Kbd>{dashboard.snapshot_version}</Kbd> 的四维回归指标。
              </AlertDescription>
            </Alert>
          ) : (
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill tone={evalReport.passed ? "success" : "danger"} mono>
                  {evalReport.passed ? "ALL GATES PASSED" : "GATE FAILED"}
                </StatusPill>
                <StatusPill tone="neutral" dot={false} mono>
                  snap: {evalReport.snapshot_version}
                </StatusPill>
                <StatusPill tone="neutral" dot={false} mono>
                  golden: {evalReport.golden_cases}
                </StatusPill>
              </div>
              <div
                ref={gatesRef}
                className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
              >
                {Object.entries(evalReport.gates).map(([name, metrics]) => (
                  <div
                    key={name}
                    data-anim="gate"
                    className="surface-inset flex flex-col gap-2 px-4 py-3.5"
                  >
                    <div className="flex items-center justify-between">
                      <div className="font-mono text-xs font-semibold uppercase text-foreground">
                        {name}
                      </div>
                      <StatusPill
                        tone={metrics.passed ? "success" : "danger"}
                        mono
                        className="py-0 text-[10px]"
                      >
                        {metrics.passed ? "PASS" : "FAIL"}
                      </StatusPill>
                    </div>
                    <div className="font-mono text-xs leading-relaxed text-muted-foreground">
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

      {/* Immutable Ledger Timeline */}
      <section className="surface-card flex flex-col overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/75 px-5 py-4">
          <div>
            <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
              不可变原始账本时间线
            </h2>
            <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              ledger/events-YYYY-MM.jsonl · Append-Only 唯一真相源 · 显示 {filteredAtoms.length} / {dashboard.counts.ledger_atoms} 条
            </p>
          </div>
          <div className="relative w-64">
            <IconSearch className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={ledgerSearch}
              onChange={(e) => {
                const next = e.target.value
                void setLedgerSearch(next, {
                  limitUrlUpdates: next === "" ? undefined : debounce(250),
                })
              }}
              placeholder="筛选账本 ID / scope / 原文..."
              className="h-8 rounded-lg pl-8 text-xs"
            />
          </div>
        </div>

        <div
          ref={ledgerRef}
          className="max-h-[calc(100dvh-21rem)] min-h-[420px] overflow-y-auto overscroll-contain"
        >
          <Table className="table-fixed">
            <TableHeader className="sticky top-0 z-10 bg-card/95 backdrop-blur">
              <TableRow className="font-mono text-[11px] uppercase">
                <TableHead className="w-40 pl-5">ID / Kind</TableHead>
                <TableHead className="w-40">Scope / Source</TableHead>
                <TableHead>Raw Content</TableHead>
                <TableHead className="w-40 pr-5 text-right">Timestamp</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredAtoms.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="py-12 text-center text-xs text-muted-foreground">
                    没有匹配的账本记录
                  </TableCell>
                </TableRow>
              ) : (
                filteredAtoms.map((atom) => {
                  const redacted = Array.isArray(atom.meta?.redacted_types)
                    ? (atom.meta.redacted_types as string[])
                    : []
                  const evRef =
                    typeof atom.meta?.evidence_ref === "string" ? atom.meta.evidence_ref : ""
                  const compactTs = atom.ts.replace("T", " ").replace(/\.\d+/, "").slice(0, 19)
                  const isLong = atom.raw.length > 220 || atom.raw.split("\n").length > 3
                  const expanded = expandedAtomIds.has(atom.id)

                  return (
                    <TableRow key={atom.id} className="align-top text-xs hover:bg-accent/30">
                      <TableCell className="space-y-1.5 pl-5">
                        <div className="truncate font-mono font-medium text-foreground" title={atom.id}>
                          {atom.id}
                        </div>
                        <div className="flex flex-wrap items-center gap-1">
                          <Badge variant="secondary" className="h-4 rounded-sm px-1.5 font-mono text-[10px]">
                            {atom.kind}
                          </Badge>
                          {redacted.length > 0 && (
                            <Badge variant="destructive" className="h-4 rounded-sm px-1.5 font-mono text-[10px]">
                              脱敏: {redacted.join(",")}
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="space-y-1 font-mono text-[11px]">
                        <div className="truncate font-medium text-chart-1" title={atom.scope}>
                          {atom.scope}
                        </div>
                        <div className="truncate text-muted-foreground" title={atom.source}>
                          {atom.source}
                        </div>
                        {evRef && (
                          <div className="truncate text-muted-foreground/80" title={evRef}>
                            {evRef}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <div className="rounded-lg border border-border/70 bg-muted/25 px-3 py-2">
                          <div
                            className={`break-words font-mono text-xs leading-relaxed whitespace-pre-wrap text-foreground ${
                              isLong && !expanded ? "line-clamp-3" : ""
                            }`}
                          >
                            {atom.raw}
                          </div>
                          {isLong && (
                            <button
                              type="button"
                              onClick={() => toggleAtomExpand(atom.id)}
                              className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-chart-1 hover:underline"
                            >
                              {expanded ? (
                                <>
                                  收起全文 <IconChevronUp className="size-3" />
                                </>
                              ) : (
                                <>
                                  展开全文 ({atom.raw.length} 字符) <IconChevronDown className="size-3" />
                                </>
                              )}
                            </button>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="pr-5 text-right font-mono text-[11px] text-muted-foreground">
                        <div className="text-foreground/80" title={atom.ts}>
                          {compactTs}
                        </div>
                        <div className="truncate" title={atom.device}>
                          {atom.device}
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
        </div>
      </section>
    </div>
  )
}
