import { useState, type FormEvent } from "react"
import { useMutation } from "@tanstack/react-query"
import { IconAlertTriangle, IconEyeCheck, IconSearch } from "@tabler/icons-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
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
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {/* Recall */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">四路混合检索实验室 (fos recall)</CardTitle>
          <CardDescription>
            FTS5 BM25 + sqlite-vec + 触发器/图谱扩散 + 双时态 --as-of 切片
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <form onSubmit={handleRecall} className="flex flex-col gap-2.5">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="输入检索问题（支持中英文、技术参数）..."
            />
            <div className="flex flex-wrap items-center gap-2">
              <Select value={recScope} onValueChange={(v) => setRecScope(v ?? "__all__")}>
                <SelectTrigger size="sm" className="w-36 font-mono text-xs">
                  <SelectValue placeholder="Scope" />
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
            <div className="flex flex-col gap-2.5">
              <div className="font-mono text-xs text-muted-foreground">
                命中 {recallRes.count} 条 · 消耗 {recallRes.used_tokens}/{recallRes.budget_tokens}{" "}
                tokens · snap={recallRes.snapshot_version}
                {recallRes.as_of ? ` · as_of=${recallRes.as_of}` : ""}
              </div>
              {recallRes.results.length === 0 ? (
                <div className="text-xs text-muted-foreground">无命中结果</div>
              ) : (
                (() => {
                  const maxScore = Math.max(0.01, ...recallRes.results.map((x) => x.score))
                  return recallRes.results.map((r) => {
                    const scorePct = Math.min(100, Math.round((r.score / maxScore) * 100))
                    return (
                      <div
                        key={r.id}
                        onClick={() => onJumpToCard(r.id)}
                        className="cursor-pointer rounded-lg border border-border bg-muted/20 p-3 transition-colors hover:border-foreground/40"
                      >
                        <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <Badge variant="outline" className="rounded-md font-mono text-xs">
                              {r.id}
                            </Badge>
                            <Badge
                              variant={r.severity === "P0" ? "destructive" : "secondary"}
                              className="rounded-md font-mono text-xs"
                            >
                              {r.severity}
                            </Badge>
                            <Badge variant="outline" className="rounded-md font-mono text-xs">
                              {r.scope}
                            </Badge>
                            {r.compat?.pattern === "code_exemplar" && (
                              <Badge variant="secondary" className="rounded-md font-mono text-xs">
                                pattern:{r.compat.lang ?? "code"}
                              </Badge>
                            )}
                            {r.freshness && r.freshness !== "untracked" && (
                              <Badge
                                variant={r.freshness === "broken_ref" ? "destructive" : "outline"}
                                className="rounded-md font-mono text-xs"
                              >
                                {r.freshness}
                              </Badge>
                            )}
                            {r.channels.map((ch) => (
                              <Badge key={ch} variant="outline" className="rounded-md font-mono text-xs">
                                {ch}
                              </Badge>
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
                          </div>
                        </div>
                        <div className="text-sm font-medium text-foreground">{r.rule}</div>
                        {r.rationale && (
                          <div className="mt-1 text-xs text-muted-foreground">{r.rationale}</div>
                        )}
                        {r.source_ref && (
                          <div className="mt-1.5 truncate font-mono text-xs text-muted-foreground">
                            {r.source_ref}
                          </div>
                        )}
                        {r.compat_check?.status === "major_drift" && (
                          <div className="mt-2 flex items-start gap-1.5 rounded-md border border-border bg-muted/50 px-2.5 py-1.5 text-xs text-foreground">
                            <IconAlertTriangle className="mt-0.5 size-3.5 shrink-0 text-destructive" />
                            <div>
                              <span className="font-mono font-medium">
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
        </CardContent>
      </Card>

      {/* Mirror */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">决策前照镜子 (fos mirror)</CardTitle>
          <CardDescription>
            并发比对红线冲突、历史错题、过往决策（含已否决方案）与未验证假设
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
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
                  <SelectValue placeholder="Scope" />
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
            <div className="flex flex-col gap-3 text-xs">
              <div className="flex flex-col gap-1.5">
                <Badge variant="destructive" className="w-fit rounded-md text-xs">
                  红线冲突 Conflicts ({mirrorRes.conflicts.length})
                </Badge>
                {mirrorRes.conflicts.length === 0 ? (
                  <div className="text-muted-foreground">无</div>
                ) : (
                  mirrorRes.conflicts.map((c) => (
                    <div
                      key={c.id}
                      onClick={() => onJumpToCard(c.id)}
                      className="cursor-pointer rounded-md border border-border bg-muted/30 p-2.5 font-mono transition-colors hover:border-foreground/40"
                    >
                      [{c.id}] {c.rule}
                    </div>
                  ))
                )}
              </div>

              <div className="flex flex-col gap-1.5">
                <Badge variant="secondary" className="w-fit rounded-md text-xs">
                  错题预警 Lesson Warnings ({mirrorRes.lesson_warnings.length})
                </Badge>
                {mirrorRes.lesson_warnings.length === 0 ? (
                  <div className="text-muted-foreground">无</div>
                ) : (
                  mirrorRes.lesson_warnings.map((w) => (
                    <div
                      key={w.id}
                      onClick={() => onJumpToCard(w.id)}
                      className="cursor-pointer rounded-md border border-border bg-muted/30 p-2.5 font-mono transition-colors hover:border-foreground/40"
                    >
                      [{w.id} | seen:{w.seen_count}] {w.rule}
                    </div>
                  ))
                )}
              </div>

              <div className="flex flex-col gap-1.5">
                <Badge variant="outline" className="w-fit rounded-md text-xs">
                  历史决策 Prior Decisions ({mirrorRes.prior_decisions.length})
                </Badge>
                {mirrorRes.prior_decisions.length === 0 ? (
                  <div className="text-muted-foreground">无</div>
                ) : (
                  mirrorRes.prior_decisions.map((d) => (
                    <div
                      key={d.id}
                      onClick={() => onJumpToCard(d.id)}
                      className="cursor-pointer rounded-md border border-border bg-muted/30 p-2.5 font-mono transition-colors hover:border-foreground/40"
                    >
                      [{d.id} | {d.status}] {d.rule}
                    </div>
                  ))
                )}
              </div>

              <div className="flex flex-col gap-1.5">
                <Badge variant="outline" className="w-fit rounded-md text-xs">
                  未验证盲点 Blind Spots ({mirrorRes.blind_spots.length})
                </Badge>
                {mirrorRes.blind_spots.length === 0 ? (
                  <div className="text-muted-foreground">无</div>
                ) : (
                  mirrorRes.blind_spots.map((b, i) => (
                    <div key={i} className="rounded-md border border-border bg-muted/30 p-2.5">
                      [{b.kind}] {b.detail}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
