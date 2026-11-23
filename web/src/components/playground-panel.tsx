import { useRef, useState, type FormEvent } from "react"
import { useMutation } from "@tanstack/react-query"
import {
  IconAlertTriangle,
  IconArrowUpRight,
  IconEyeCheck,
  IconSearch,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { usePanelTransition } from "@/lib/motion"
import type { DashboardResponse, MirrorResponse, RecallResponse } from "@/lib/types"

interface PlaygroundPanelProps {
  dashboard: DashboardResponse
  onJumpToCard: (nodeId: string) => void
}

export function PlaygroundPanel({ dashboard, onJumpToCard }: PlaygroundPanelProps) {
  const [query, setQuery] = useState("")
  const [recScope, setRecScope] = useState("__all__")
  const [asOf, setAsOf] = useState("")
  const [cwdStack, setCwdStack] = useState("")
  const [budget, setBudget] = useState("1200")
  const [recallRes, setRecallRes] = useState<RecallResponse | null>(null)

  const [proposal, setProposal] = useState("")
  const [mirrorScope, setMirrorScope] = useState("__all__")
  const [mirrorRes, setMirrorRes] = useState<MirrorResponse | null>(null)
  const recallResRef = useRef<HTMLDivElement | null>(null)
  const mirrorResRef = useRef<HTMLDivElement | null>(null)

  usePanelTransition(recallResRef, [recallRes], { y: 6, duration: 0.22 })
  usePanelTransition(mirrorResRef, [mirrorRes], { y: 6, duration: 0.22 })

  const recallMutation = useMutation({
    mutationFn: () =>
      fosApi.recall({
        q: query.trim(),
        scope: recScope === "__all__" ? undefined : recScope,
        asOf: asOf.trim() || undefined,
        budget: Number(budget) || 1200,
        cwdStack: cwdStack.trim() || undefined,
      }),
    onSuccess: (res) => setRecallRes(res),
    onError: (err: Error) => toast.error(err.message),
  })

  const mirrorMutation = useMutation({
    mutationFn: () =>
      fosApi.mirror(proposal.trim(), mirrorScope === "__all__" ? undefined : mirrorScope),
    onSuccess: (res) => setMirrorRes(res),
    onError: (err: Error) => toast.error(err.message),
  })

  const handleRecall = (e: FormEvent) => {
    e.preventDefault()
    if (!query.trim()) return
    recallMutation.mutate()
  }

  const handleMirror = (e: FormEvent) => {
    e.preventDefault()
    if (!proposal.trim()) return
    mirrorMutation.mutate()
  }

  return (
    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
      {/* Recall */}
      <section className="surface-card flex flex-col gap-4 p-5">
        <div className="flex items-center gap-3">
          <span className="icon-tile size-9 text-chart-1">
            <IconSearch className="size-4" />
          </span>
          <div>
            <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
              四路混合检索实验室 (fos recall)
            </h2>
            <p className="text-xs text-muted-foreground">
              FTS5 BM25 + sqlite-vec + 触发器/图谱扩散 + 双时态 --as-of 切片
            </p>
          </div>
        </div>

        <form onSubmit={handleRecall} className="flex flex-col gap-2.5">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="输入检索问题（支持中英文、技术参数）..."
          />
          <div className="flex flex-wrap items-center gap-2">
            <Select value={recScope} onValueChange={(v) => setRecScope(v ?? "__all__")}>
              <SelectTrigger size="sm" className="w-36 font-mono text-xs">
                <SelectValue>
                  {(v: string | null) => (!v || v === "__all__" ? "全部 Scope" : v)}
                </SelectValue>
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
            <Input
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              placeholder="--as-of 如 2026-08"
              className="h-8 w-36 font-mono text-xs"
            />
            <Input
              type="number"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              className="h-8 w-20 font-mono text-xs"
              min={100}
              max={8000}
            />
            <Button type="submit" size="sm" disabled={recallMutation.isPending}>
              <IconSearch className="size-3.5" />
              {recallMutation.isPending ? "检索中..." : "执行 Recall"}
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={cwdStack}
              onChange={(e) => setCwdStack(e.target.value)}
              placeholder="模拟当前项目栈（可选，如 kotlin=2.4,spring-boot=5.0）"
              className="h-8 flex-1 font-mono text-xs"
            />
            <Button
              type="button"
              variant="outline"
              size="xs"
              className="font-normal text-xs"
              onClick={() => {
                setQuery("Kotlin WebFlux zero-copy streaming low memory")
                setCwdStack("kotlin=2.4,spring-boot=5.0,webflux=5.0")
              }}
            >
              填入 Boot 5.0 漂移示例
            </Button>
          </div>
        </form>

        {recallRes && (
          <div ref={recallResRef} className="flex flex-col gap-2.5 border-t border-border/80 pt-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2 font-mono text-xs text-muted-foreground">
              <span>
                命中 <strong className="text-foreground">{recallRes.count}</strong> 条 · 消耗{" "}
                {recallRes.used_tokens}/{recallRes.budget_tokens}t
              </span>
              <span>
                snap={recallRes.snapshot_version}
                {recallRes.as_of ? ` · as_of=${recallRes.as_of}` : ""}
              </span>
            </div>
            {recallRes.results.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border py-8 text-center text-xs text-muted-foreground">
                无命中结果
              </div>
            ) : (
              (() => {
                const maxScore = Math.max(0.01, ...recallRes.results.map((x) => x.score))
                return recallRes.results.map((r) => {
                  const scorePct = Math.min(100, Math.round((r.score / maxScore) * 100))
                  const stripe =
                    r.severity === "P0"
                      ? "border-l-destructive"
                      : r.compat?.pattern === "code_exemplar"
                        ? "border-l-chart-1"
                        : "border-l-chart-2"
                  return (
                    <div
                      key={r.id}
                      onClick={() => onJumpToCard(r.id)}
                      className={`interactive-card group cursor-pointer rounded-xl border border-border/80 border-l-[3px] bg-card p-3.5 hover:border-foreground/20 ${stripe}`}
                    >
                      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-mono text-xs font-semibold text-foreground">
                            {r.id}
                          </span>
                          <Badge
                            variant={r.severity === "P0" ? "destructive" : "secondary"}
                            className="h-4 rounded-sm px-1.5 font-mono text-[10px]"
                          >
                            {r.severity}
                          </Badge>
                          <span className="font-mono text-[11px] text-chart-1">{r.scope}</span>
                          {r.compat?.pattern === "code_exemplar" && (
                            <Badge variant="outline" className="h-4 rounded-sm px-1.5 font-mono text-[10px] text-chart-1">
                              pattern:{r.compat.lang ?? "code"}
                            </Badge>
                          )}
                          {r.freshness && r.freshness !== "untracked" && (
                            <Badge
                              variant={r.freshness === "broken_ref" ? "destructive" : "outline"}
                              className="h-4 rounded-sm px-1.5 font-mono text-[10px]"
                            >
                              {r.freshness}
                            </Badge>
                          )}
                          {r.channels.map((ch) => (
                            <span
                              key={ch}
                              className="rounded bg-muted/70 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                            >
                              {ch}
                            </span>
                          ))}
                        </div>
                        <div className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
                          <div className="h-1.5 w-14 overflow-hidden rounded-full bg-muted">
                            <div
                              className="h-full rounded-full bg-chart-1"
                              style={{ width: `${scorePct}%` }}
                            />
                          </div>
                          <span>{r.score}</span>
                          <IconArrowUpRight className="size-3.5 opacity-0 transition-opacity group-hover:opacity-100" />
                        </div>
                      </div>
                      <div className="text-sm font-semibold leading-snug text-foreground">
                        {r.rule}
                      </div>
                      {r.rationale && (
                        <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
                          {r.rationale}
                        </div>
                      )}
                      {r.source_ref && (
                        <div className="mt-1.5 truncate font-mono text-[11px] text-muted-foreground/85">
                          {r.source_ref}
                        </div>
                      )}
                      {r.compat_check?.status === "major_drift" && (
                        <div className="mt-2 flex items-start gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-foreground">
                          <IconAlertTriangle className="mt-0.5 size-3.5 shrink-0 text-destructive" />
                          <div>
                            <span className="font-mono font-medium text-destructive">
                              SemVer major_drift ({(r.compat_check.drifts ?? []).join(", ")})：
                            </span>
                            <span className="text-muted-foreground">{r.compat_check.note}</span>
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })
              })()
            )}
          </div>
        )}
      </section>

      {/* Mirror */}
      <section className="surface-card flex flex-col gap-4 p-5">
        <div className="flex items-center gap-3">
          <span className="icon-tile size-9 text-chart-2">
            <IconEyeCheck className="size-4" />
          </span>
          <div>
            <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
              决策前照镜子 (fos mirror)
            </h2>
            <p className="text-xs text-muted-foreground">
              并发比对红线冲突、历史错题、过往决策（含已否决方案）与未验证假设
            </p>
          </div>
        </div>

        <form onSubmit={handleMirror} className="flex flex-col gap-2.5">
          <Textarea
            rows={3}
            value={proposal}
            onChange={(e) => setProposal(e.target.value)}
            placeholder="输入你的新方案、技术选型或部署计划，例如：把上游用量字段直接按整数解析..."
          />
          <div className="flex flex-wrap items-center gap-2">
            <Select value={mirrorScope} onValueChange={(v) => setMirrorScope(v ?? "__all__")}>
              <SelectTrigger size="sm" className="w-36 font-mono text-xs">
                <SelectValue>
                  {(v: string | null) => (!v || v === "__all__" ? "全部 Scope" : v)}
                </SelectValue>
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
            <Button type="submit" size="sm" disabled={mirrorMutation.isPending}>
              <IconEyeCheck className="size-3.5" />
              {mirrorMutation.isPending ? "审查中..." : "照镜子检查"}
            </Button>
          </div>
        </form>

        {mirrorRes && (
          <div ref={mirrorResRef} className="flex flex-col gap-3 border-t border-border/80 pt-3.5 text-xs">
            {/* Conflicts */}
            <div
              className={`flex flex-col gap-2 rounded-xl border border-border border-l-[3px] p-3 ${
                mirrorRes.conflicts.length > 0
                  ? "border-l-destructive bg-destructive/5"
                  : "border-l-border bg-muted/20"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-foreground">红线冲突 Conflicts</span>
                <Badge
                  variant={mirrorRes.conflicts.length > 0 ? "destructive" : "outline"}
                  className="h-5 font-mono text-[11px]"
                >
                  {mirrorRes.conflicts.length}
                </Badge>
              </div>
              {mirrorRes.conflicts.length === 0 ? (
                <div className="text-muted-foreground">未检测到 P0 红线冲突</div>
              ) : (
                mirrorRes.conflicts.map((c) => (
                  <div
                    key={c.id}
                    onClick={() => onJumpToCard(c.id)}
                    className="group flex cursor-pointer items-start justify-between gap-2 rounded-lg border border-border/80 bg-card px-3 py-2 transition-colors hover:border-foreground/30"
                  >
                    <div className="leading-relaxed text-foreground">
                      <span className="mr-1.5 font-mono font-semibold text-destructive">
                        {c.id}
                      </span>
                      {c.rule}
                    </div>
                    <IconArrowUpRight className="mt-0.5 size-3.5 shrink-0 text-muted-foreground opacity-50 group-hover:opacity-100" />
                  </div>
                ))
              )}
            </div>

            {/* Lesson Warnings */}
            <div
              className={`flex flex-col gap-2 rounded-xl border border-border border-l-[3px] p-3 ${
                mirrorRes.lesson_warnings.length > 0
                  ? "border-l-chart-3 bg-chart-3/5"
                  : "border-l-border bg-muted/20"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-foreground">错题预警 Lesson Warnings</span>
                <Badge variant="secondary" className="h-5 font-mono text-[11px]">
                  {mirrorRes.lesson_warnings.length}
                </Badge>
              </div>
              {mirrorRes.lesson_warnings.length === 0 ? (
                <div className="text-muted-foreground">无相关历史踩坑</div>
              ) : (
                mirrorRes.lesson_warnings.map((w) => (
                  <div
                    key={w.id}
                    onClick={() => onJumpToCard(w.id)}
                    className="group flex cursor-pointer items-start justify-between gap-2 rounded-lg border border-border/80 bg-card px-3 py-2 transition-colors hover:border-foreground/30"
                  >
                    <div className="leading-relaxed text-foreground">
                      <span className="mr-1.5 font-mono font-semibold text-chart-3">
                        {w.id}
                      </span>
                      <span className="mr-1.5 font-mono text-[11px] text-muted-foreground">
                        seen:{w.seen_count}
                      </span>
                      {w.rule}
                    </div>
                    <IconArrowUpRight className="mt-0.5 size-3.5 shrink-0 text-muted-foreground opacity-50 group-hover:opacity-100" />
                  </div>
                ))
              )}
            </div>

            {/* Prior Decisions */}
            <div
              className={`flex flex-col gap-2 rounded-xl border border-border border-l-[3px] p-3 ${
                mirrorRes.prior_decisions.length > 0
                  ? "border-l-chart-1 bg-chart-1/5"
                  : "border-l-border bg-muted/20"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-foreground">历史决策 Prior Decisions</span>
                <Badge variant="outline" className="h-5 font-mono text-[11px]">
                  {mirrorRes.prior_decisions.length}
                </Badge>
              </div>
              {mirrorRes.prior_decisions.length === 0 ? (
                <div className="text-muted-foreground">无关联过往决策</div>
              ) : (
                mirrorRes.prior_decisions.map((d) => (
                  <div
                    key={d.id}
                    onClick={() => onJumpToCard(d.id)}
                    className="group flex cursor-pointer items-start justify-between gap-2 rounded-lg border border-border/80 bg-card px-3 py-2 transition-colors hover:border-foreground/30"
                  >
                    <div className="leading-relaxed text-foreground">
                      <span className="mr-1.5 font-mono font-semibold text-chart-1">
                        {d.id}
                      </span>
                      <span className="mr-1.5 font-mono text-[11px] text-muted-foreground">
                        {d.status}
                      </span>
                      {d.rule}
                    </div>
                    <IconArrowUpRight className="mt-0.5 size-3.5 shrink-0 text-muted-foreground opacity-50 group-hover:opacity-100" />
                  </div>
                ))
              )}
            </div>

            {/* Blind Spots */}
            <div className="flex flex-col gap-2 rounded-xl border border-border border-l-[3px] border-l-muted-foreground/40 bg-muted/20 p-3">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-foreground">未验证盲点 Blind Spots</span>
                <Badge variant="outline" className="h-5 font-mono text-[11px]">
                  {mirrorRes.blind_spots.length}
                </Badge>
              </div>
              {mirrorRes.blind_spots.length === 0 ? (
                <div className="text-muted-foreground">无</div>
              ) : (
                mirrorRes.blind_spots.map((b, i) => (
                  <div
                    key={i}
                    className="rounded-lg border border-border/70 bg-card px-3 py-2 leading-relaxed text-foreground"
                  >
                    <span className="mr-1.5 font-mono text-[11px] text-muted-foreground">
                      [{b.kind}]
                    </span>
                    {b.detail}
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
