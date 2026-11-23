import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { debounce, useQueryStates } from "nuqs"
import {
  IconAdjustmentsHorizontal,
  IconArchive,
  IconArrowRight,
  IconChartBar,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconCircleDot,
  IconClock,
  IconCopy,
  IconEdit,
  IconFlag,
  IconFolder,
  IconGitBranch,
  IconPin,
  IconSearch,
  IconShieldCheck,
  IconTag,
  IconThumbDown,
  IconThumbUp,
  IconX,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { ScopeCombobox } from "@/components/shared/scope-combobox"
import { ScopeTabBar } from "@/components/shared/scope-tab-bar"
import { StatusPill, type StatusTone } from "@/components/shared/status-pill"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { fosApi } from "@/lib/api"
import { usePanelTransition, useRevealGroup } from "@/lib/motion"
import { SCOPE_ALL, matchesScopeFilter } from "@/lib/scope-tree"
import {
  cardsFilterParsers,
  type CardKindFilter,
  type CardSeverityFilter,
  type CardSpecialFilter,
  type CardStatusFilter,
} from "@/lib/url-state"
import type { CompiledNode, DashboardResponse, EditNodeInput } from "@/lib/types"

interface EditFormState {
  type: string
  severity: string
  scope: string
  rule: string
  rationale: string
  triggerWhen: string
  entities: string
  guardHint: string
  guardPattern: string
  sourceRef: string
}

const EMPTY_EDIT_FORM: EditFormState = {
  type: "lesson",
  severity: "P1",
  scope: "global",
  rule: "",
  rationale: "",
  triggerWhen: "",
  entities: "",
  guardHint: "",
  guardPattern: "",
  sourceRef: "",
}

function parseTagList(raw: string): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const part of raw.split(/[,，、\n]/)) {
    const clean = part.trim()
    if (clean && !seen.has(clean)) {
      seen.add(clean)
      out.push(clean)
    }
  }
  return out
}

function sameStringList(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false
  }
  return true
}

function nodeToEditForm(node: CompiledNode): EditFormState {
  return {
    type: node.type || "lesson",
    severity: node.severity || "P1",
    scope: node.scope || "global",
    rule: node.rule || "",
    rationale: node.rationale || "",
    triggerWhen: (node.trigger_when || []).join(", "),
    entities: (node.entities || []).join(", "),
    guardHint: node.guard_hint || "",
    guardPattern: node.guard_pattern || "",
    sourceRef: node.source_ref || "",
  }
}

function buildEditDiff(target: CompiledNode, form: EditFormState): EditNodeInput {
  const diff: EditNodeInput = {}
  const rule = form.rule.trim()
  const rationale = form.rationale.trim()
  const scope = form.scope.trim()
  const guardHint = form.guardHint.trim()
  const guardPattern = form.guardPattern.trim()
  const sourceRef = form.sourceRef.trim()
  const triggerWhen = parseTagList(form.triggerWhen)
  const entities = parseTagList(form.entities)

  if (form.type !== target.type) diff.type = form.type
  if (form.severity !== target.severity) diff.severity = form.severity
  if (scope !== target.scope) diff.scope = scope
  if (rule !== target.rule) diff.rule = rule
  if (rationale !== (target.rationale || "")) diff.rationale = rationale
  if (guardHint !== (target.guard_hint || "")) diff.guard_hint = guardHint
  if (guardPattern !== (target.guard_pattern || "")) diff.guard_pattern = guardPattern
  if (sourceRef !== (target.source_ref || "")) diff.source_ref = sourceRef
  if (!sameStringList(triggerWhen, target.trigger_when || [])) diff.trigger_when = triggerWhen
  if (!sameStringList(entities, target.entities || [])) diff.entities = entities

  return diff
}

interface CardsPanelProps {
  dashboard: DashboardResponse
  selectedNodeId: string | null
  onSelectNode: (id: string) => void
}

const STATUS_LABELS: Record<string, string> = {
  active: "Active（活跃）",
  __all__: "全部状态",
  superseded: "Superseded",
  archived: "Archived",
}

const SEVERITY_LABELS: Record<string, string> = {
  __all__: "全部优先级",
  P0: "P0 红线",
  P1: "P1 重要",
  P2: "P2 窄约束",
}

const KIND_LABELS: Record<string, string> = {
  __all__: "全部类型",
  principle: "principle",
  lesson: "lesson",
  decision: "decision",
  thought: "thought",
  outcome: "outcome",
}

const SPECIAL_LABELS: Record<string, string> = {
  all: "全部专题",
  patterns: "代码范式 Patterns",
  issues: "漂移·断链 Issues",
  guards: "静态守卫 Guards",
}

function statusToneFor(status: string): StatusTone {
  if (status === "active") return "success"
  if (status === "superseded") return "warning"
  return "neutral"
}

function severityToneFor(sev: string): StatusTone {
  if (sev === "P0") return "danger"
  if (sev === "P1") return "warning"
  return "neutral"
}

function freshnessToneFor(freshness?: string): StatusTone {
  if (freshness === "fresh") return "success"
  if (freshness === "drifted") return "warning"
  if (freshness === "broken_ref") return "danger"
  return "neutral"
}

function CopyableCodeStrip({
  text,
  prefix,
  copyText,
}: {
  text: string
  prefix?: string
  copyText?: string
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(copyText ?? text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error("复制失败")
    }
  }

  return (
    <div className="surface-inset group flex items-start gap-2 px-3.5 py-2.5 transition-colors hover:bg-muted/50">
      <div className="min-w-0 flex-1 overflow-x-auto font-mono text-xs leading-relaxed text-foreground">
        {prefix && <span className="mr-1.5 text-muted-foreground select-none">{prefix}</span>}
        <span className="whitespace-pre-wrap break-words">{text}</span>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        onClick={handleCopy}
        aria-label="复制"
        className="shrink-0 text-muted-foreground opacity-65 transition-opacity hover:opacity-100"
      >
        {copied ? <IconCheck className="size-3.5 text-chart-2" /> : <IconCopy className="size-3.5" />}
      </Button>
    </div>
  )
}

function parseEvidenceRaw(raw: string): { prefix: string; lang: string; code: string; suffix: string } | null {
  const m = raw.match(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/)
  if (!m || m.index === undefined) return null
  const prefix = raw.slice(0, m.index).trim()
  const lang = m[1] || "code"
  const code = m[2].replace(/\n$/, "")
  const suffix = raw.slice(m.index + m[0].length).trim()
  return { prefix, lang, code, suffix }
}

function EvidenceBlock({ raw }: { raw: string }) {
  const [copied, setCopied] = useState(false)
  const [showMeta, setShowMeta] = useState(false)
  const parsed = useMemo(() => parseEvidenceRaw(raw), [raw])

  if (!parsed) {
    return (
      <div className="rounded-lg border border-border/80 bg-muted/30 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words text-foreground">
        {raw}
      </div>
    )
  }

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(parsed.code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      toast.error("复制失败")
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {parsed.prefix && (
        <div className="overflow-hidden rounded-lg border border-border/70 bg-muted/20">
          <button
            type="button"
            onClick={() => setShowMeta((v) => !v)}
            className="flex w-full items-center justify-between px-3 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
          >
            <span className="flex items-center gap-1.5 font-medium">
              {showMeta ? <IconChevronDown className="size-3.5" /> : <IconChevronRight className="size-3.5" />}
              原始结构化元信息
            </span>
            <span className="font-mono text-[11px]">{showMeta ? "收起" : "展开"}</span>
          </button>
          {showMeta && (
            <div className="border-t border-border/70 px-3 py-2.5 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words text-muted-foreground">
              {parsed.prefix}
            </div>
          )}
        </div>
      )}
      <div className="overflow-hidden rounded-lg border border-border/80 bg-background">
        <div className="flex items-center justify-between border-b border-border/80 bg-muted/35 px-3 py-1.5">
          <Badge variant="outline" className="h-5 rounded-md bg-background font-mono text-[11px]">
            {parsed.lang}
          </Badge>
          <Button variant="ghost" size="xs" className="font-normal text-xs" onClick={copyCode}>
            {copied ? <IconCheck className="size-3.5 text-chart-2" /> : <IconCopy className="size-3.5" />}
            {copied ? "已复制" : "复制骨架"}
          </Button>
        </div>
        <pre className="overflow-x-auto p-3.5 font-mono text-xs leading-relaxed text-foreground">
          <code>{parsed.code}</code>
        </pre>
      </div>
      {parsed.suffix && (
        <div className="rounded-lg border border-border/70 bg-muted/25 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words text-muted-foreground">
          {parsed.suffix}
        </div>
      )}
    </div>
  )
}

export function CardsPanel({ dashboard, selectedNodeId, onSelectNode }: CardsPanelProps) {
  const [filters, setFilters] = useQueryStates(cardsFilterParsers)
  const { q: search, scope, status, severity, kind, special } = filters

  const [editOpen, setEditOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<CompiledNode | null>(null)
  const [editForm, setEditForm] = useState<EditFormState>(EMPTY_EDIT_FORM)

  const [correctTarget, setCorrectTarget] = useState<CompiledNode | null>(null)
  const [correctRule, setCorrectRule] = useState("")
  const [correctReason, setCorrectReason] = useState("")

  const [archiveTarget, setArchiveTarget] = useState<CompiledNode | null>(null)
  const [archiveReason, setArchiveReason] = useState("")

  const queryClient = useQueryClient()
  const listRef = useRef<HTMLDivElement | null>(null)
  const detailBodyRef = useRef<HTMLDivElement | null>(null)
  const detailContentRef = useRef<HTMLDivElement | null>(null)

  const nodeIdSet = useMemo(() => new Set(dashboard.nodes.map((n) => n.id)), [dashboard.nodes])

  const nodesMatchingSecondaryFilters = useMemo(() => {
    const q = search.trim().toLowerCase()
    return dashboard.nodes.filter((n) => {
      if (status !== "__all__" && n.status !== status) return false
      if (severity !== "__all__" && n.severity !== severity) return false
      if (kind !== "__all__" && n.type !== kind) return false
      if (special === "patterns" && n.compat?.pattern !== "code_exemplar") return false
      if (special === "issues" && n.freshness !== "drifted" && n.freshness !== "broken_ref") return false
      if (special === "guards" && !n.guard_pattern) return false
      if (q) {
        const hay = [
          n.id,
          n.rule,
          n.rationale,
          n.guard_hint,
          n.source_ref ?? "",
          ...(n.trigger_when || []),
          ...(n.entities || []),
        ]
          .join(" ")
          .toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [dashboard.nodes, search, status, severity, kind, special])

  const secondaryNodeScopes = useMemo(
    () => nodesMatchingSecondaryFilters.map((n) => n.scope),
    [nodesMatchingSecondaryFilters]
  )

  const filteredNodes = useMemo(
    () => nodesMatchingSecondaryFilters.filter((n) => matchesScopeFilter(n.scope, scope)),
    [nodesMatchingSecondaryFilters, scope]
  )

  const activeFilterCount =
    (status !== "active" ? 1 : 0) +
    (severity !== "__all__" ? 1 : 0) +
    (kind !== "__all__" ? 1 : 0) +
    (special !== "all" ? 1 : 0)

  const resetSecondaryFilters = () => {
    void setFilters({
      status: "active",
      severity: "__all__",
      kind: "__all__",
      special: "all",
    })
  }

  const detailQuery = useQuery({
    queryKey: ["fos", "node", selectedNodeId],
    queryFn: () => fosApi.getNode(selectedNodeId!, "L2", true),
    enabled: Boolean(selectedNodeId),
  })

  useEffect(() => {
    detailBodyRef.current?.scrollTo({ top: 0 })
  }, [selectedNodeId])

  const pinMutation = useMutation({
    mutationFn: (id: string) => fosApi.pinNode(id),
    onSuccess: (res) => {
      toast.success(`已将 ${res.pinned_node} 钉入永久 P0 回归集`)
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const feedbackMutation = useMutation({
    mutationFn: (input: { id: string; verdict: "useful" | "noise" }) =>
      fosApi.feedbackNode(input.id, input.verdict),
    onSuccess: (res, vars) => {
      toast.success(
        `${vars.verdict === "useful" ? "已记为有用" : "已记为噪声"}（效用分 ${(res.utility_score * 100).toFixed(0)}%）`
      )
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const editDiff = useMemo(
    () => (editTarget ? buildEditDiff(editTarget, editForm) : {}),
    [editTarget, editForm]
  )
  const editChangeCount = Object.keys(editDiff).length
  const parsedEditTriggers = useMemo(() => parseTagList(editForm.triggerWhen), [editForm.triggerWhen])
  const parsedEditEntities = useMemo(() => parseTagList(editForm.entities), [editForm.entities])

  const openEditDialog = (node: CompiledNode) => {
    setEditTarget(node)
    setEditForm(nodeToEditForm(node))
    setEditOpen(true)
  }

  const editMutation = useMutation({
    mutationFn: () => fosApi.editNode(editTarget!.id, editDiff),
    onSuccess: (res) => {
      if (res.changed_fields.length > 0) {
        toast.success(`已保存 ${res.node.id}（更新 ${res.changed_fields.length} 项）`)
      } else {
        toast.info("内容未发生变化")
      }
      setEditOpen(false)
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const correctMutation = useMutation({
    mutationFn: () => fosApi.correctNode(correctTarget!.id, correctRule, correctReason),
    onSuccess: (res) => {
      toast.success(`已纠偏 ${res.superseded} → ${res.node.id}`)
      setCorrectTarget(null)
      onSelectNode(res.node.id)
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const archiveMutation = useMutation({
    mutationFn: () => fosApi.archiveNode(archiveTarget!.id, archiveReason),
    onSuccess: (res) => {
      toast.success(`已归档 ${res.archived}`)
      setArchiveTarget(null)
      void queryClient.invalidateQueries({ queryKey: ["fos"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const handleEditSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!editTarget || !editForm.rule.trim() || !editForm.scope.trim() || editChangeCount === 0) return
    editMutation.mutate()
  }

  const handleEditKeyDown = (e: KeyboardEvent<HTMLFormElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault()
      if (!editTarget || !editForm.rule.trim() || !editForm.scope.trim() || editChangeCount === 0 || editMutation.isPending) {
        return
      }
      editMutation.mutate()
    }
  }

  const handleCorrectSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!correctTarget || !correctRule.trim()) return
    correctMutation.mutate()
  }

  const handleArchiveSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!archiveTarget) return
    archiveMutation.mutate()
  }

  const detailNode = detailQuery.data?.node
  useRevealGroup(listRef, "[data-anim='card-row']", [scope, status, severity, kind, special, filteredNodes.length], {
    y: 6,
    duration: 0.28,
    maxItems: 22,
  })
  usePanelTransition(detailContentRef, [detailNode?.id], { x: 0, y: 5, duration: 0.26 })

  return (
    <div className="flex flex-col gap-3">
      {/* Compact Single-Row Progressive Scope Tabs + Search & Filter Popover */}
      <div className="surface-card flex items-center justify-between gap-2.5 px-2.5 py-1.5">
        <ScopeTabBar
          value={scope}
          onChange={(next) => void setFilters({ scope: next })}
          allScopes={dashboard.scopes}
          nodeScopes={secondaryNodeScopes}
          className="flex-1"
        />

        <div className="flex shrink-0 items-center gap-1.5">
          <div className="relative w-40 sm:w-52">
            <IconSearch className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => {
                const next = e.target.value
                void setFilters(
                  { q: next },
                  { limitUrlUpdates: next === "" ? undefined : debounce(250) }
                )
              }}
              placeholder="搜索规则 / 触发词..."
              className="h-8 rounded-lg pr-7 pl-8 text-xs"
            />
            {search && (
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="清空搜索"
                onClick={() => void setFilters({ q: "" })}
                className="absolute top-1/2 right-1 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <IconX className="size-3" />
              </Button>
            )}
          </div>

          <Popover>
            <PopoverTrigger
              render={
                <Button
                  type="button"
                  variant={activeFilterCount > 0 ? "secondary" : "outline"}
                  size="sm"
                  className="h-8 gap-1.5 rounded-lg px-2.5 text-xs font-normal"
                />
              }
            >
              <IconAdjustmentsHorizontal className="size-3.5" />
              <span>筛选</span>
              {activeFilterCount > 0 && (
                <span className="inline-flex size-4 items-center justify-center rounded-full bg-foreground font-mono text-[10px] font-medium text-background">
                  {activeFilterCount}
                </span>
              )}
            </PopoverTrigger>
            <PopoverContent align="end" sideOffset={6} className="w-72 gap-3.5 rounded-2xl p-4">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground">高级筛选</span>
                {activeFilterCount > 0 && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    onClick={resetSecondaryFilters}
                    className="h-6 px-2 text-xs text-muted-foreground hover:text-foreground"
                  >
                    重置
                  </Button>
                )}
              </div>

              <div className="flex flex-col gap-2.5">
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] font-medium text-muted-foreground">状态 Status</span>
                  <Select
                    value={status}
                    onValueChange={(v) =>
                      void setFilters({ status: (v ?? SCOPE_ALL) as CardStatusFilter })
                    }
                  >
                    <SelectTrigger size="sm" className="w-full rounded-lg text-xs">
                      <SelectValue>{(v: string | null) => (v ? STATUS_LABELS[v] ?? v : "状态")}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="active">Active（活跃）</SelectItem>
                      <SelectItem value="__all__">全部状态</SelectItem>
                      <SelectItem value="superseded">Superseded</SelectItem>
                      <SelectItem value="archived">Archived</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex flex-col gap-1">
                  <span className="text-[11px] font-medium text-muted-foreground">优先级 Severity</span>
                  <Select
                    value={severity}
                    onValueChange={(v) =>
                      void setFilters({ severity: (v ?? SCOPE_ALL) as CardSeverityFilter })
                    }
                  >
                    <SelectTrigger size="sm" className="w-full rounded-lg text-xs">
                      <SelectValue>{(v: string | null) => (v ? SEVERITY_LABELS[v] ?? v : "优先级")}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__all__">全部优先级</SelectItem>
                      <SelectItem value="P0">P0 红线</SelectItem>
                      <SelectItem value="P1">P1 重要</SelectItem>
                      <SelectItem value="P2">P2 窄约束</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex flex-col gap-1">
                  <span className="text-[11px] font-medium text-muted-foreground">类型 Kind</span>
                  <Select
                    value={kind}
                    onValueChange={(v) =>
                      void setFilters({ kind: (v ?? SCOPE_ALL) as CardKindFilter })
                    }
                  >
                    <SelectTrigger size="sm" className="w-full rounded-lg text-xs">
                      <SelectValue>{(v: string | null) => (v ? KIND_LABELS[v] ?? v : "类型")}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__all__">全部类型</SelectItem>
                      <SelectItem value="principle">principle</SelectItem>
                      <SelectItem value="lesson">lesson</SelectItem>
                      <SelectItem value="decision">decision</SelectItem>
                      <SelectItem value="thought">thought</SelectItem>
                      <SelectItem value="outcome">outcome</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex flex-col gap-1">
                  <span className="text-[11px] font-medium text-muted-foreground">专题 Special</span>
                  <Select
                    value={special}
                    onValueChange={(v) =>
                      void setFilters({ special: (v ?? "all") as CardSpecialFilter })
                    }
                  >
                    <SelectTrigger size="sm" className="w-full rounded-lg text-xs">
                      <SelectValue>{(v: string | null) => (v ? SPECIAL_LABELS[v] ?? v : "专题")}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">全部专题</SelectItem>
                      <SelectItem value="patterns">代码范式 Patterns</SelectItem>
                      <SelectItem value="issues">漂移·断链 Issues</SelectItem>
                      <SelectItem value="guards">静态守卫 Guards</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {/* Master-Detail Split */}
      <div className="grid grid-cols-1 gap-4 lg:h-[calc(100dvh-8.75rem)] lg:min-h-[540px] lg:grid-cols-12 lg:overflow-hidden">
        {/* Left Card List */}
        <div className="surface-card flex flex-col overflow-hidden lg:col-span-6 lg:h-full">
          <div
            ref={listRef}
            className="flex-1 divide-y divide-border/60 overflow-y-auto overscroll-contain"
          >
            {filteredNodes.length === 0 ? (
              <div className="py-16 text-center text-sm text-muted-foreground">
                当前筛选条件下没有匹配的记忆卡片。
              </div>
            ) : (
              filteredNodes.map((n) => {
                const active = n.id === selectedNodeId
                return (
                  <div
                    key={n.id}
                    data-anim="card-row"
                    onClick={() => onSelectNode(n.id)}
                    className={`relative flex cursor-pointer flex-col gap-1.5 px-4 py-3.5 transition-colors duration-150 ${
                      active ? "bg-muted/65" : "hover:bg-muted/35"
                    }`}
                  >
                    {active && (
                      <span
                        aria-hidden="true"
                        className="absolute top-3 bottom-3 left-1 w-[3px] rounded-full bg-foreground"
                      />
                    )}
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-mono text-xs font-semibold text-foreground">{n.id}</span>
                        <StatusPill
                          tone={severityToneFor(n.severity)}
                          dot={n.severity === "P0"}
                          mono
                          className="py-0 text-[10px]"
                        >
                          {n.severity}
                        </StatusPill>
                        <span className="font-mono text-[11px] text-muted-foreground">{n.type}</span>
                        <StatusPill tone="info" dot={false} mono className="py-0 text-[10px]">
                          {n.scope}
                        </StatusPill>
                        {n.status !== "active" && (
                          <StatusPill tone={statusToneFor(n.status)} dot={false} className="py-0 text-[10px]">
                            {n.status}
                          </StatusPill>
                        )}
                        {n.pinned && (
                          <StatusPill tone="danger" dot={false} mono className="py-0 text-[10px]">
                            PINNED
                          </StatusPill>
                        )}
                        {n.compat?.pattern === "code_exemplar" && (
                          <StatusPill tone="purple" dot={false} mono className="py-0 text-[10px]">
                            pattern{n.compat.lang ? `:${n.compat.lang}` : ""}
                          </StatusPill>
                        )}
                      </div>
                      <div className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
                        {n.freshness && n.freshness !== "untracked" && (
                          <span
                            title={`freshness: ${n.freshness}`}
                            className={`size-1.5 rounded-full ${
                              n.freshness === "fresh"
                                ? "bg-chart-2"
                                : n.freshness === "drifted"
                                  ? "bg-chart-3"
                                  : "bg-chart-5"
                            }`}
                          />
                        )}
                        {(n.recall_hits ?? 0) > 0 && <span>hits:{n.recall_hits}</span>}
                        <span>seen:{n.seen_count} · {(n.valid_from || "").slice(0, 10)}</span>
                      </div>
                    </div>
                    <div className="text-[14px] font-medium leading-6 text-foreground">{n.rule}</div>
                    {n.rationale && (
                      <div className="line-clamp-2 text-[13px] leading-relaxed text-muted-foreground">
                        {n.rationale}
                      </div>
                    )}
                    {n.guard_hint && (
                      <div className="flex items-start gap-1.5 text-xs text-chart-2">
                        <IconShieldCheck className="mt-0.5 size-3.5 shrink-0" />
                        <span className="line-clamp-1">{n.guard_hint}</span>
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* Right Detail Panel — Linear-style Calm Property Sheet */}
        <div className="surface-card flex flex-col overflow-hidden lg:col-span-6 lg:h-full">
          {!selectedNodeId ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 p-10 text-center text-sm text-muted-foreground">
              <span className="icon-tile size-10 text-muted-foreground">
                <IconFolder className="size-4" />
              </span>
              <p className="max-w-xs text-xs leading-relaxed">
                点击左侧任意记忆卡片，查看 L1/L2 无损原文证据、双时态关系，或执行编辑 / 纠偏 / 归档 / 钉选。
              </p>
            </div>
          ) : detailQuery.isLoading ? (
            <div className="flex flex-1 flex-col gap-4 p-6">
              <div className="flex items-center justify-between gap-2">
                <Skeleton className="h-5 w-40 rounded-md" />
                <Skeleton className="h-6 w-48 rounded-md" />
              </div>
              <Skeleton className="h-8 w-5/6 rounded-md" />
              <div className="grid grid-cols-[100px_1fr] gap-3 pt-2">
                <Skeleton className="h-4 w-16" />
                <Skeleton className="h-5 w-24 rounded-full" />
                <Skeleton className="h-4 w-16" />
                <Skeleton className="h-5 w-32 rounded-full" />
              </div>
              <Separator />
              <Skeleton className="h-24 w-full rounded-xl" />
              <Skeleton className="h-40 w-full rounded-xl" />
            </div>
          ) : detailNode ? (
            <div ref={detailContentRef} className="flex min-h-0 flex-1 flex-col">
              {/* Calm Top Breadcrumb & Actions Header */}
              <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border/70 px-6 py-3">
                <div className="flex min-w-0 items-center gap-1.5 font-mono text-xs text-muted-foreground">
                  <IconFolder className="size-3.5 shrink-0 opacity-70" />
                  <span className="truncate">{detailNode.scope}</span>
                  <span className="opacity-40">/</span>
                  <span className="font-semibold text-foreground">{detailNode.id}</span>
                </div>
                <div className="flex flex-wrap items-center gap-1">
                  <Button
                    variant="ghost"
                    size="xs"
                    className="rounded-lg text-muted-foreground hover:text-foreground"
                    disabled={feedbackMutation.isPending}
                    onClick={() =>
                      feedbackMutation.mutate({ id: detailNode.id, verdict: "useful" })
                    }
                  >
                    <IconThumbUp className="size-3.5" />
                    有用
                  </Button>
                  <Button
                    variant="ghost"
                    size="xs"
                    className="rounded-lg text-muted-foreground hover:text-foreground"
                    disabled={feedbackMutation.isPending}
                    onClick={() =>
                      feedbackMutation.mutate({ id: detailNode.id, verdict: "noise" })
                    }
                  >
                    <IconThumbDown className="size-3.5" />
                    噪声
                  </Button>
                  <Button
                    variant="ghost"
                    size="xs"
                    className="rounded-lg text-muted-foreground hover:text-foreground"
                    disabled={pinMutation.isPending}
                    onClick={() => pinMutation.mutate(detailNode.id)}
                  >
                    <IconPin className="size-3.5" />
                    Pin
                  </Button>
                  <Button
                    variant="outline"
                    size="xs"
                    className="rounded-lg"
                    onClick={() => openEditDialog(detailNode)}
                  >
                    <IconEdit className="size-3.5" />
                    编辑
                  </Button>
                  <Button
                    variant="outline"
                    size="xs"
                    className="rounded-lg"
                    onClick={() => {
                      setCorrectTarget(detailNode)
                      setCorrectRule(detailNode.rule)
                      setCorrectReason("")
                    }}
                  >
                    <IconGitBranch className="size-3.5" />
                    纠偏
                  </Button>
                  <Button
                    variant="destructive"
                    size="xs"
                    className="rounded-lg"
                    onClick={() => {
                      setArchiveTarget(detailNode)
                      setArchiveReason("")
                    }}
                  >
                    <IconArchive className="size-3.5" />
                    归档
                  </Button>
                </div>
              </div>

              {/* Single Smooth Scrollable Body */}
              <div
                ref={detailBodyRef}
                className="flex flex-1 flex-col gap-6 overflow-y-auto overscroll-contain px-6 py-5"
              >
                {/* Title + Structured Property Grid */}
                <div className="flex flex-col gap-4">
                  <h3 className="font-heading text-[18px] font-semibold leading-snug tracking-tight text-foreground">
                    {detailNode.rule}
                  </h3>

                  <div className="grid grid-cols-[96px_1fr] items-center gap-x-3 gap-y-2.5 text-xs">
                    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                      <IconCircleDot className="size-3.5 opacity-70" />
                      状态
                    </span>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <StatusPill tone={statusToneFor(detailNode.status)}>
                        {detailNode.status}
                      </StatusPill>
                      {detailNode.freshness && detailNode.freshness !== "untracked" && (
                        <StatusPill tone={freshnessToneFor(detailNode.freshness)} mono>
                          {detailNode.freshness}
                        </StatusPill>
                      )}
                      {detailNode.graduated && (
                        <StatusPill tone="purple" dot={false} mono>
                          graduated
                        </StatusPill>
                      )}
                    </div>

                    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                      <IconFlag className="size-3.5 opacity-70" />
                      优先级
                    </span>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <StatusPill tone={severityToneFor(detailNode.severity)}>
                        {SEVERITY_LABELS[detailNode.severity] ?? detailNode.severity}
                      </StatusPill>
                      <StatusPill tone="purple" dot={false} mono>
                        {detailNode.type}
                      </StatusPill>
                    </div>

                    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                      <IconFolder className="size-3.5 opacity-70" />
                      作用域
                    </span>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <StatusPill tone="info" dot={false} mono>
                        {detailNode.scope}
                      </StatusPill>
                    </div>

                    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                      <IconChartBar className="size-3.5 opacity-70" />
                      效用命中
                    </span>
                    <div className="font-mono text-xs text-foreground/85">
                      Seen {detailNode.seen_count}× · hits {detailNode.recall_hits ?? 0} · utility{" "}
                      {Math.round((detailNode.utility_score ?? 0.5) * 100)}% (+
                      {detailNode.useful_count ?? 0}/-{detailNode.noise_count ?? 0})
                    </div>

                    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                      <IconClock className="size-3.5 opacity-70" />
                      有效窗口
                    </span>
                    <div className="font-mono text-xs text-muted-foreground">
                      <span className="text-foreground/85">
                        {(detailNode.valid_from || "").slice(0, 19)}
                      </span>{" "}
                      → {detailNode.valid_until ? detailNode.valid_until.slice(0, 19) : "present"}
                    </div>

                    {((detailNode.trigger_when || []).length > 0 ||
                      (detailNode.entities || []).length > 0) && (
                      <>
                        <span className="inline-flex items-center gap-1.5 self-start pt-0.5 text-muted-foreground">
                          <IconTag className="size-3.5 opacity-70" />
                          触发 / 标签
                        </span>
                        <div className="flex flex-wrap gap-1.5">
                          {(detailNode.trigger_when || []).map((t) => (
                            <span
                              key={`tw-${t}`}
                              className="rounded-md bg-muted/75 px-2 py-0.5 text-xs text-foreground/85"
                            >
                              {t}
                            </span>
                          ))}
                          {(() => {
                            const seenTriggers = new Set(
                              (detailNode.trigger_when || []).map((t) => t.toLowerCase())
                            )
                            return (detailNode.entities || [])
                              .filter((ent) => !seenTriggers.has(ent.toLowerCase()))
                              .map((ent) => (
                                <span
                                  key={`ent-${ent}`}
                                  className="rounded-md border border-border/70 bg-background px-2 py-0.5 font-mono text-[11px] text-muted-foreground"
                                >
                                  {ent}
                                </span>
                              ))
                          })()}
                        </div>
                      </>
                    )}
                  </div>
                </div>

                <Separator className="opacity-65" />

                {detailNode.superseded_by && (
                  <div className="flex items-center justify-between rounded-lg border border-border bg-muted/40 px-3.5 py-2.5 text-xs">
                    <span className="text-muted-foreground">
                      已被新规则替代：<span className="font-mono font-medium text-foreground">{detailNode.superseded_by}</span>
                    </span>
                    <Button
                      variant="outline"
                      size="xs"
                      onClick={() => onSelectNode(detailNode.superseded_by!)}
                    >
                      跳转查看
                    </Button>
                  </div>
                )}

                {detailNode.rationale && (
                  <section className="flex flex-col gap-1.5">
                    <div className="text-[11px] font-medium tracking-wide text-muted-foreground">
                      原因 Rationale
                    </div>
                    <p className="text-sm leading-relaxed text-foreground">{detailNode.rationale}</p>
                  </section>
                )}

                {detailNode.source_ref && (
                  <section className="flex flex-col gap-1.5">
                    <div className="text-[11px] font-medium tracking-wide text-muted-foreground">
                      权威源指针 Source Pointer
                    </div>
                    <CopyableCodeStrip text={detailNode.source_ref} />
                  </section>
                )}

                {detailNode.compat && Object.keys(detailNode.compat).length > 0 && (
                  <section className="flex flex-col gap-2">
                    <div className="text-[11px] font-medium tracking-wide text-muted-foreground">
                      框架版本包络 Compat Envelope (SemVer)
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {detailNode.compat.lang && (
                        <Badge variant="outline" className="rounded-md font-mono text-xs">
                          {detailNode.compat.lang}
                        </Badge>
                      )}
                      {Object.entries(detailNode.compat.stack ?? {}).map(([k, v]) => (
                        <Badge key={k} variant="secondary" className="rounded-md font-mono text-xs">
                          {k} {v}
                        </Badge>
                      ))}
                    </div>
                    {detailNode.compat.bench_cmd && (
                      <CopyableCodeStrip
                        prefix="verify:"
                        text={detailNode.compat.bench_cmd}
                        copyText={detailNode.compat.bench_cmd}
                      />
                    )}
                  </section>
                )}

                {detailNode.guard_pattern && (
                  <section className="flex flex-col gap-1.5">
                    <div className="text-[11px] font-medium tracking-wide text-muted-foreground">
                      确定性静态守卫 Guard Pattern (fos guard)
                    </div>
                    <CopyableCodeStrip text={detailNode.guard_pattern} />
                  </section>
                )}

                {detailNode.guard_hint && (
                  <section className="flex flex-col gap-1.5">
                    <div className="text-[11px] font-medium tracking-wide text-muted-foreground">
                      守卫建议 Guard Test
                    </div>
                    <div className="surface-inset p-3.5 text-xs leading-relaxed text-foreground">
                      {detailNode.guard_hint}
                    </div>
                  </section>
                )}

                <section className="flex flex-col gap-2">
                  <div className="text-[11px] font-medium tracking-wide text-muted-foreground">
                    双时态关系边 ({detailQuery.data?.graph?.length ?? 0})
                  </div>
                  {(detailQuery.data?.graph ?? []).length === 0 ? (
                    <div className="text-xs text-muted-foreground">无显式边</div>
                  ) : (
                    <div className="surface-inset divide-y divide-border/60 overflow-hidden font-mono text-xs">
                      {(detailQuery.data?.graph ?? []).map((e, i) => {
                        const targetClickable = nodeIdSet.has(e.dst_id) && e.dst_id !== detailNode.id
                        return (
                          <div
                            key={i}
                            className="flex flex-wrap items-center gap-2 px-3.5 py-2 text-foreground"
                          >
                            <span className="font-medium">{e.src_id}</span>
                            <IconArrowRight className="size-3 text-muted-foreground" />
                            <StatusPill tone="neutral" dot={false} mono className="py-0 text-[10px]">
                              {e.edge_type}
                            </StatusPill>
                            <IconArrowRight className="size-3 text-muted-foreground" />
                            {targetClickable ? (
                              <Button
                                type="button"
                                variant="link"
                                size="xs"
                                onClick={() => onSelectNode(e.dst_id)}
                                className="h-auto p-0 font-mono text-xs font-medium text-chart-1"
                              >
                                {e.dst_id}
                              </Button>
                            ) : (
                              <span className="text-muted-foreground">{e.dst_id}</span>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </section>

                <Separator />

                <section className="flex flex-col gap-2.5 pb-2">
                  <div className="text-[11px] font-medium tracking-wide text-muted-foreground">
                    L2 不可变原始证据 ({detailQuery.data?.evidence?.length ?? 0})
                  </div>
                  <div className="flex flex-col gap-3">
                    {(detailQuery.data?.evidence ?? []).map((a) => (
                      <div key={a.id} className="flex flex-col gap-2">
                        <div className="font-mono text-[11px] text-muted-foreground">
                          {a.id} · {a.source} · {a.ts}
                        </div>
                        <EvidenceBlock raw={a.raw} />
                      </div>
                    ))}
                  </div>
                </section>
              </div>
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center p-10 text-center text-sm text-destructive">
              未能加载节点详情
            </div>
          )}
        </div>
      </div>

      {/* Edit Card Dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              编辑知识卡片
              {editTarget && (
                <Badge variant="outline" className="rounded-sm font-mono text-xs font-medium">
                  {editTarget.id}
                </Badge>
              )}
            </DialogTitle>
            <DialogDescription>
              原地更新当前卡片的规则与元信息（节点 ID、图谱关系与历史演进保持不变，变更写入不可变账本）。若旧规则已被推翻需保留演进版本，请使用「纠偏」。
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleEditSubmit} onKeyDown={handleEditKeyDown} className="flex flex-col gap-4">
            <div className="flex max-h-[62vh] flex-col gap-3.5 overflow-y-auto pr-1">
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-12">
                <div className="flex flex-col gap-1 sm:col-span-4">
                  <span className="text-[11px] font-medium text-muted-foreground">类型 Type</span>
                  <Select
                    value={editForm.type}
                    onValueChange={(v) => setEditForm((f) => ({ ...f, type: v ?? f.type }))}
                  >
                    <SelectTrigger size="sm" className="w-full text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="principle">principle</SelectItem>
                      <SelectItem value="lesson">lesson</SelectItem>
                      <SelectItem value="decision">decision</SelectItem>
                      <SelectItem value="thought">thought</SelectItem>
                      <SelectItem value="outcome">outcome</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex flex-col gap-1 sm:col-span-3">
                  <span className="text-[11px] font-medium text-muted-foreground">优先级</span>
                  <Select
                    value={editForm.severity}
                    onValueChange={(v) => setEditForm((f) => ({ ...f, severity: v ?? f.severity }))}
                  >
                    <SelectTrigger size="sm" className="w-full font-mono text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="P0">P0 红线</SelectItem>
                      <SelectItem value="P1">P1 重要</SelectItem>
                      <SelectItem value="P2">P2 窄约束</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex flex-col gap-1 sm:col-span-5">
                  <span className="text-[11px] font-medium text-muted-foreground">作用域 Scope</span>
                  <ScopeCombobox
                    value={editForm.scope}
                    onChange={(next) => setEditForm((f) => ({ ...f, scope: next }))}
                    scopes={dashboard.scopes}
                  />
                </div>
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-[11px] font-medium text-muted-foreground">规则 Rule *</span>
                <Textarea
                  rows={3}
                  value={editForm.rule}
                  onChange={(e) => setEditForm((f) => ({ ...f, rule: e.target.value }))}
                  placeholder="清晰可执行的规则或红线..."
                  className="text-sm leading-relaxed"
                />
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-[11px] font-medium text-muted-foreground">原因 Rationale</span>
                <Textarea
                  rows={2}
                  value={editForm.rationale}
                  onChange={(e) => setEditForm((f) => ({ ...f, rationale: e.target.value }))}
                  placeholder="结构性根因或权衡理由（选填）"
                  className="text-xs leading-relaxed"
                />
              </div>

              <Separator />

              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-medium text-muted-foreground">
                  触发条件 trigger_when（逗号分隔）
                </span>
                <Input
                  value={editForm.triggerWhen}
                  onChange={(e) => setEditForm((f) => ({ ...f, triggerWhen: e.target.value }))}
                  placeholder="例如：修改画布视口, pointercancel, 缩放异常"
                  className="h-8 text-xs"
                />
                {parsedEditTriggers.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {parsedEditTriggers.map((t) => (
                      <Badge key={t} variant="secondary" className="rounded-md text-[11px] font-normal">
                        {t}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-medium text-muted-foreground">
                  实体标签 entities（逗号分隔）
                </span>
                <Input
                  value={editForm.entities}
                  onChange={(e) => setEditForm((f) => ({ ...f, entities: e.target.value }))}
                  placeholder="例如：canvas, viewport, raf"
                  className="h-8 font-mono text-xs"
                />
                {parsedEditEntities.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {parsedEditEntities.map((ent) => (
                      <Badge key={ent} variant="outline" className="rounded-md font-mono text-[11px] font-normal">
                        {ent}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-[11px] font-medium text-muted-foreground">守卫建议 Guard Hint</span>
                <Input
                  value={editForm.guardHint}
                  onChange={(e) => setEditForm((f) => ({ ...f, guardHint: e.target.value }))}
                  placeholder="如何用单测 / lint / CI 固化检查（选填）"
                  className="h-8 text-xs"
                />
              </div>

              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] font-medium text-muted-foreground">
                    静态守卫 Guard Pattern
                  </span>
                  <Input
                    value={editForm.guardPattern}
                    onChange={(e) => setEditForm((f) => ({ ...f, guardPattern: e.target.value }))}
                    placeholder="path:*.tsx :: pattern"
                    className="h-8 font-mono text-xs"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] font-medium text-muted-foreground">
                    权威源指针 Source Pointer
                  </span>
                  <Input
                    value={editForm.sourceRef}
                    onChange={(e) => setEditForm((f) => ({ ...f, sourceRef: e.target.value }))}
                    placeholder="/abs/path/file.ts::Symbol"
                    className="h-8 font-mono text-xs"
                  />
                </div>
              </div>
            </div>

            <DialogFooter className="items-center sm:justify-between">
              <span className="text-xs text-muted-foreground">
                {editChangeCount > 0 ? `已修改 ${editChangeCount} 项 · ⌘/Ctrl + Enter 保存` : "暂无未保存修改"}
              </span>
              <div className="flex items-center gap-2">
                <Button type="button" variant="outline" onClick={() => setEditOpen(false)}>
                  取消
                </Button>
                <Button
                  type="submit"
                  disabled={
                    !editForm.rule.trim() ||
                    !editForm.scope.trim() ||
                    editChangeCount === 0 ||
                    editMutation.isPending
                  }
                >
                  {editMutation.isPending ? "保存中..." : "保存修改"}
                </Button>
              </div>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Correct Dialog */}
      <Dialog open={Boolean(correctTarget)} onOpenChange={(o) => !o && setCorrectTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>自我纠正 {correctTarget?.id}</DialogTitle>
            <DialogDescription>
              旧节点将被标记为 superseded（默认检索 0% 召回，保留 --as-of 历史），新规则自动继承 kind 与 scope。
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCorrectSubmit} className="flex flex-col gap-3">
            <Textarea
              rows={3}
              value={correctRule}
              onChange={(e) => setCorrectRule(e.target.value)}
              placeholder="输入修正后的精确规则..."
            />
            <Input
              value={correctReason}
              onChange={(e) => setCorrectReason(e.target.value)}
              placeholder="纠偏原因（例如：边界条件更新 / 旧假设证伪）"
            />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setCorrectTarget(null)}>
                取消
              </Button>
              <Button type="submit" disabled={correctMutation.isPending}>
                {correctMutation.isPending ? "保存中..." : "保存纠偏"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Archive Dialog */}
      <Dialog open={Boolean(archiveTarget)} onOpenChange={(o) => !o && setArchiveTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>归档节点 {archiveTarget?.id}</DialogTitle>
            <DialogDescription>
              归档后从 boot / recall / mirror 隐藏，但原始账本与 --as-of 历史永久可查。
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleArchiveSubmit} className="flex flex-col gap-3">
            <Input
              value={archiveReason}
              onChange={(e) => setArchiveReason(e.target.value)}
              placeholder="归档原因（例如：已固化为 CI Guard Test / 功能已下线）"
            />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setArchiveTarget(null)}>
                取消
              </Button>
              <Button type="submit" variant="destructive" disabled={archiveMutation.isPending}>
                {archiveMutation.isPending ? "归档中..." : "确认归档"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
