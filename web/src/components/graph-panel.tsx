import { useEffect, useMemo, useRef, useState, type MouseEvent, type WheelEvent } from "react"
import {
  IconArrowUpRight,
  IconFocusCentered,
  IconMinus,
  IconPlus,
  IconSearch,
} from "@tabler/icons-react"
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
import type { CompiledNode, DashboardResponse, GraphEdge } from "@/lib/types"

interface GraphPanelProps {
  dashboard: DashboardResponse
  onJumpToCard: (nodeId: string) => void
  themeVersion: number
}

interface Palette {
  bg: string
  card: string
  mutedBg: string
  border: string
  fg: string
  muted: string
  c1: string
  c2: string
  c3: string
  c4: string
  c5: string
}

function readThemePalette(): Palette {
  const cs = getComputedStyle(document.documentElement)
  const get = (name: string, fb: string) => cs.getPropertyValue(name).trim() || fb
  return {
    bg: get("--background", "#111"),
    card: get("--card", "#1b1b22"),
    mutedBg: get("--muted", "#222"),
    border: get("--border", "#333"),
    fg: get("--foreground", "#eee"),
    muted: get("--muted-foreground", "#888"),
    c1: get("--chart-1", "#60a5fa"),
    c2: get("--chart-2", "#34d399"),
    c3: get("--chart-3", "#fbbf24"),
    c4: get("--chart-4", "#a78bfa"),
    c5: get("--chart-5", "#f87171"),
  }
}

const NODE_W = 224
const NODE_H = 56
const COL_GAP = 48
const ROW_GAP = 16
const PAD_X = 36
const HEADER_H = 52

export function GraphPanel({ dashboard, onJumpToCard, themeVersion }: GraphPanelProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [scopeFilter, setScopeFilter] = useState<string>("__all__")
  const [viewMode, setViewMode] = useState<"all" | "connected">("all")
  const [includeHistory, setIncludeHistory] = useState<boolean>(true)
  const [search, setSearch] = useState<string>("")
  const [palette, setPalette] = useState<Palette>(() => readThemePalette())

  const [zoom, setZoom] = useState<number>(0.9)
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 16, y: 12 })
  const [dragging, setDragging] = useState(false)
  const dragOrigin = useRef<{ mx: number; my: number; px: number; py: number } | null>(null)
  const viewportRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    setPalette(readThemePalette())
  }, [themeVersion])

  const semanticEdges = useMemo(
    () => dashboard.edges.filter((e) => !e.dst_id.startsWith("scope:")),
    [dashboard.edges]
  )

  const connectedNodeIds = useMemo(() => {
    const s = new Set<string>()
    for (const e of semanticEdges) {
      s.add(e.src_id)
      s.add(e.dst_id)
    }
    return s
  }, [semanticEdges])

  const filteredNodes = useMemo(() => {
    const q = search.trim().toLowerCase()
    return dashboard.nodes.filter((n) => {
      if (scopeFilter !== "__all__" && n.scope !== scopeFilter) return false
      if (!includeHistory && n.status !== "active") return false
      if (viewMode === "connected" && !connectedNodeIds.has(n.id)) return false
      if (q) {
        const hay = `${n.id} ${n.scope} ${n.type} ${n.rule} ${(n.entities || []).join(" ")}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [dashboard.nodes, scopeFilter, includeHistory, viewMode, connectedNodeIds, search])

  const layout = useMemo(() => {
    const nodeSet = new Set(filteredNodes.map((n) => n.id))
    const scopes = [...new Set(filteredNodes.map((n) => n.scope))].sort((a, b) =>
      a === "global" ? -1 : b === "global" ? 1 : a.localeCompare(b)
    )

    const colW = NODE_W + COL_GAP
    const positions: Record<string, { x: number; y: number; node: CompiledNode }> = {}
    const lanes: Array<{ scope: string; x: number; w: number; h: number; count: number }> = []
    let maxRows = 1

    scopes.forEach((sc, ci) => {
      const group = filteredNodes
        .filter((n) => n.scope === sc)
        .sort((a, b) => {
          const rank = (x: CompiledNode) =>
            x.status !== "active" ? 4 : x.type === "principle" ? 0 : x.compat?.pattern === "code_exemplar" ? 1 : x.severity === "P0" ? 2 : 3
          return rank(a) - rank(b) || a.id.localeCompare(b.id)
        })
      maxRows = Math.max(maxRows, group.length)
      const laneX = PAD_X + ci * colW
      const cx = laneX + NODE_W / 2
      const laneH = HEADER_H + group.length * (NODE_H + ROW_GAP) + 14
      lanes.push({ scope: sc, x: laneX - 12, w: NODE_W + 24, h: laneH, count: group.length })
      group.forEach((n, ri) => {
        positions[n.id] = {
          x: cx,
          y: HEADER_H + ri * (NODE_H + ROW_GAP) + NODE_H / 2 + 8,
          node: n,
        }
      })
    })

    const width = Math.max(760, PAD_X * 2 + scopes.length * colW - COL_GAP)
    const height = Math.max(420, HEADER_H + maxRows * (NODE_H + ROW_GAP) + 48)
    const visibleEdges = semanticEdges.filter((e) => nodeSet.has(e.src_id) && nodeSet.has(e.dst_id))
    return { width, height, positions, lanes, edges: visibleEdges }
  }, [filteredNodes, semanticEdges])

  const fitToView = () => {
    const vp = viewportRef.current
    if (!vp) {
      setZoom(0.9)
      setPan({ x: 16, y: 12 })
      return
    }
    const vw = vp.clientWidth || 960
    const vh = vp.clientHeight || 540
    const sx = (vw - 32) / Math.max(1, layout.width)
    const sy = (vh - 24) / Math.max(1, layout.height)
    const nextZoom = Math.min(1.0, Math.max(0.42, Math.min(sx, sy)))
    setZoom(Number(nextZoom.toFixed(2)))
    setPan({
      x: Math.max(12, Math.round((vw - layout.width * nextZoom) / 2)),
      y: 12,
    })
  }

  // Build 1-hop neighborhood for hover/select focus
  const focusId = hoveredId ?? selectedId
  const focusNeighbors = useMemo(() => {
    if (!focusId) return null
    const set = new Set<string>([focusId])
    for (const e of layout.edges) {
      if (e.src_id === focusId) set.add(e.dst_id)
      if (e.dst_id === focusId) set.add(e.src_id)
    }
    return set
  }, [focusId, layout.edges])

  const selectedNode = useMemo(
    () => (selectedId ? dashboard.nodes.find((n) => n.id === selectedId) ?? null : null),
    [selectedId, dashboard.nodes]
  )

  const selectedEdges = useMemo<GraphEdge[]>(
    () =>
      selectedId
        ? semanticEdges.filter((e) => e.src_id === selectedId || e.dst_id === selectedId)
        : [],
    [selectedId, semanticEdges]
  )

  const strokeFor = (n: CompiledNode) => {
    if (n.status === "archived") return palette.muted
    if (n.status === "superseded") return palette.c3
    if (n.compat?.pattern === "code_exemplar") return palette.c1
    if (n.type === "principle") return palette.c4
    return palette.c2
  }

  const edgeColorFor = (edgeType: string) => {
    if (edgeType === "SUPERSEDES") return palette.c3
    if (edgeType === "CONTRADICTS") return palette.c5
    return palette.c1
  }

  const edgeMarkerFor = (edgeType: string) => {
    if (edgeType === "SUPERSEDES") return "url(#fos-arrow-sup)"
    if (edgeType === "CONTRADICTS") return "url(#fos-arrow-con)"
    return "url(#fos-arrow-rel)"
  }

  const handleWheel = (e: WheelEvent<HTMLDivElement>) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault()
      const factor = e.deltaY < 0 ? 1.1 : 0.9
      setZoom((z) => Number(Math.min(1.8, Math.max(0.35, z * factor)).toFixed(2)))
    }
  }

  const handleMouseDown = (e: MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("[data-graph-node='true']")) return
    setDragging(true)
    dragOrigin.current = { mx: e.clientX, my: e.clientY, px: pan.x, py: pan.y }
  }

  const handleMouseMove = (e: MouseEvent<HTMLDivElement>) => {
    if (!dragging || !dragOrigin.current) return
    const dx = e.clientX - dragOrigin.current.mx
    const dy = e.clientY - dragOrigin.current.my
    setPan({ x: dragOrigin.current.px + dx, y: dragOrigin.current.py + dy })
  }

  const stopDrag = () => {
    setDragging(false)
    dragOrigin.current = null
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium text-foreground">双时态知识图谱</h2>
            <p className="text-xs text-muted-foreground">
              拖拽平移 · 按住 ⌘/Ctrl 滚轮缩放 · 悬停高亮 1-hop 演进与冲突邻域（显示 {filteredNodes.length} 节点 / {layout.edges.length} 语义边）
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2.5 rounded-full bg-chart-4" />
              principle
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2.5 rounded-full bg-chart-1" />
              code pattern
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2.5 rounded-full bg-chart-2" />
              active
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2.5 rounded-full bg-chart-3" />
              superseded
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2.5 rounded-full bg-chart-5" />
              conflict / alert
            </span>
          </div>
        </div>

        {/* Interactive Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative w-48">
              <IconSearch className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索节点 / 规则..."
                className="h-8 pl-8 text-xs"
              />
            </div>

            <Select value={scopeFilter} onValueChange={(v) => setScopeFilter(v ?? "__all__")}>
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

            <Button
              variant={viewMode === "connected" ? "secondary" : "outline"}
              size="sm"
              className="font-normal text-xs"
              onClick={() => setViewMode((m) => (m === "all" ? "connected" : "all"))}
            >
              {viewMode === "connected" ? "仅关系子图" : "全部节点"}
            </Button>

            <Button
              variant={includeHistory ? "secondary" : "outline"}
              size="sm"
              className="font-normal text-xs"
              onClick={() => setIncludeHistory((h) => !h)}
            >
              {includeHistory ? "含历史演进" : "仅活跃节点"}
            </Button>
          </div>

          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="缩小"
              onClick={() => setZoom((z) => Number(Math.max(0.35, z - 0.15).toFixed(2)))}
            >
              <IconMinus className="size-3.5" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="w-14 font-mono text-xs font-normal"
              onClick={() => {
                setZoom(1)
                setPan({ x: 16, y: 12 })
              }}
            >
              {Math.round(zoom * 100)}%
            </Button>
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="放大"
              onClick={() => setZoom((z) => Number(Math.min(1.8, z + 0.15).toFixed(2)))}
            >
              <IconPlus className="size-3.5" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="font-normal text-xs"
              onClick={fitToView}
            >
              <IconFocusCentered className="size-3.5" />
              适应
            </Button>
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <div
          ref={viewportRef}
          onWheel={handleWheel}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={stopDrag}
          onMouseLeave={stopDrag}
          className={`relative h-[calc(100vh-230px)] min-h-[540px] overflow-hidden rounded-sm border border-border bg-card/40 select-none ${
            dragging ? "cursor-grabbing" : "cursor-grab"
          }`}
        >
          {filteredNodes.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              当前筛选条件下没有可展示的图谱节点（可切换为「全部节点」或清空搜索词）。
            </div>
          ) : (
            <svg
              width={layout.width}
              height={layout.height}
              style={{
                transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                transformOrigin: "0 0",
              }}
              className="block"
            >
              <defs>
                <marker
                  id="fos-arrow-rel"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 1 L 10 5 L 0 9 z" fill={palette.c1} />
                </marker>
                <marker
                  id="fos-arrow-sup"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 1 L 10 5 L 0 9 z" fill={palette.c3} />
                </marker>
                <marker
                  id="fos-arrow-con"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 1 L 10 5 L 0 9 z" fill={palette.c5} />
                </marker>
              </defs>

              {/* Scope swimlane backdrops */}
              {layout.lanes.map((lane) => (
                <g key={lane.scope}>
                  <rect
                    x={lane.x}
                    y={14}
                    width={lane.w}
                    height={lane.h}
                    rx={12}
                    fill={palette.mutedBg}
                    fillOpacity={0.35}
                    stroke={palette.border}
                    strokeWidth={1}
                  />
                  <text
                    x={lane.x + 14}
                    y={36}
                    fill={palette.fg}
                    fontSize={12}
                    fontFamily="monospace"
                    fontWeight={500}
                  >
                    {lane.scope}
                  </text>
                  <text
                    x={lane.x + lane.w - 14}
                    y={36}
                    textAnchor="end"
                    fill={palette.muted}
                    fontSize={11}
                    fontFamily="monospace"
                  >
                    {lane.count}
                  </text>
                </g>
              ))}

              {/* Semantic relationship edges */}
              {layout.edges.map((e, idx) => {
                const a = layout.positions[e.src_id]
                const b = layout.positions[e.dst_id]
                if (!a || !b) return null
                const edgeColor = edgeColorFor(e.edge_type)
                const isIncident = focusId ? e.src_id === focusId || e.dst_id === focusId : true
                const sameCol = Math.abs(a.x - b.x) < 4
                const startX = sameCol ? a.x + NODE_W / 2 : a.x < b.x ? a.x + NODE_W / 2 : a.x - NODE_W / 2
                const endX = sameCol ? b.x + NODE_W / 2 : a.x < b.x ? b.x - NODE_W / 2 : b.x + NODE_W / 2
                const bend = sameCol ? 54 + (idx % 3) * 16 : 0
                const mx = (startX + endX) / 2 + bend
                const my = (a.y + b.y) / 2 - (sameCol ? 0 : 18)
                return (
                  <g key={`${e.src_id}-${e.dst_id}-${e.edge_type}-${idx}`} opacity={isIncident ? 0.92 : 0.14}>
                    <path
                      d={`M ${startX} ${a.y} Q ${mx} ${my} ${endX} ${b.y}`}
                      fill="none"
                      stroke={edgeColor}
                      strokeWidth={isIncident && focusId ? 2.2 : 1.5}
                      strokeDasharray={e.edge_type === "CONTRADICTS" ? "5 3" : undefined}
                      markerEnd={edgeMarkerFor(e.edge_type)}
                    />
                    <text
                      x={mx}
                      y={my - 4}
                      textAnchor="middle"
                      fill={edgeColor}
                      fontSize={9.5}
                      fontFamily="monospace"
                    >
                      {e.edge_type}
                    </text>
                  </g>
                )
              })}

              {/* Nodes */}
              {Object.values(layout.positions).map(({ x, y, node }) => {
                const stroke = strokeFor(node)
                const label = node.rule.length > 20 ? `${node.rule.slice(0, 20)}…` : node.rule
                const isSelected = selectedId === node.id
                const isDimmed = Boolean(focusNeighbors && !focusNeighbors.has(node.id))
                const isPattern = node.compat?.pattern === "code_exemplar"
                const hasAlert = node.freshness === "drifted" || node.freshness === "broken_ref"
                return (
                  <g
                    key={node.id}
                    data-graph-node="true"
                    onClick={() => setSelectedId(node.id)}
                    onMouseEnter={() => setHoveredId(node.id)}
                    onMouseLeave={() => setHoveredId((prev) => (prev === node.id ? null : prev))}
                    className="cursor-pointer"
                    opacity={isDimmed ? 0.22 : node.status === "archived" ? 0.6 : 1}
                  >
                    <rect
                      x={x - NODE_W / 2}
                      y={y - NODE_H / 2}
                      width={NODE_W}
                      height={NODE_H}
                      rx={8}
                      fill={palette.card}
                      stroke={stroke}
                      strokeWidth={isSelected ? 2.4 : 1.3}
                      strokeDasharray={node.status !== "active" ? "4 3" : undefined}
                    />
                    {/* Left accent bar */}
                    <rect
                      x={x - NODE_W / 2}
                      y={y - NODE_H / 2 + 6}
                      width={3.5}
                      height={NODE_H - 12}
                      rx={2}
                      fill={stroke}
                    />
                    <text
                      x={x - NODE_W / 2 + 12}
                      y={y - 7}
                      fill={palette.fg}
                      fontSize={11}
                      fontFamily="monospace"
                      fontWeight={500}
                    >
                      {node.id} · {node.severity}
                      {isPattern ? " · PAT" : ""}
                    </text>
                    {hasAlert && (
                      <circle
                        cx={x + NODE_W / 2 - 12}
                        cy={y - 11}
                        r={4}
                        fill={palette.c5}
                      />
                    )}
                    <text
                      x={x - NODE_W / 2 + 12}
                      y={y + 13}
                      fill={palette.muted}
                      fontSize={11}
                    >
                      {label}
                    </text>
                  </g>
                )
              })}
            </svg>
          )}
        </div>

        {/* Selected Node Inspector */}
        {selectedNode && (
          <div className="flex flex-col gap-2.5 rounded-lg border border-border bg-muted/30 px-4 py-3 text-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex flex-col gap-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline" className="rounded-md font-mono text-xs">
                    {selectedNode.id}
                  </Badge>
                  <Badge
                    variant={selectedNode.severity === "P0" ? "destructive" : "secondary"}
                    className="rounded-md font-mono text-xs"
                  >
                    {selectedNode.severity}
                  </Badge>
                  <Badge variant="outline" className="rounded-md text-xs">
                    {selectedNode.type}
                  </Badge>
                  <Badge variant="outline" className="rounded-md font-mono text-xs">
                    {selectedNode.scope}
                  </Badge>
                  {selectedNode.compat?.pattern === "code_exemplar" && (
                    <Badge variant="secondary" className="rounded-md font-mono text-xs">
                      pattern:{selectedNode.compat.lang ?? "code"}
                    </Badge>
                  )}
                  {selectedNode.freshness && selectedNode.freshness !== "untracked" && (
                    <Badge
                      variant={selectedNode.freshness === "broken_ref" ? "destructive" : "outline"}
                      className="rounded-md font-mono text-xs"
                    >
                      {selectedNode.freshness}
                    </Badge>
                  )}
                </div>
                <div className="text-foreground">{selectedNode.rule}</div>
                {selectedNode.source_ref && (
                  <div className="font-mono text-xs text-muted-foreground break-all">
                    {selectedNode.source_ref}
                  </div>
                )}
              </div>
              <Button variant="outline" size="sm" onClick={() => onJumpToCard(selectedNode.id)}>
                查看 L2 证据与操作
                <IconArrowUpRight className="size-3.5" />
              </Button>
            </div>

            {selectedEdges.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 border-t border-border pt-2 text-xs">
                <span className="text-muted-foreground">关联边 ({selectedEdges.length})：</span>
                {selectedEdges.map((e, i) => {
                  const other = e.src_id === selectedNode.id ? e.dst_id : e.src_id
                  const arrow = e.src_id === selectedNode.id ? `→ [${e.edge_type}] →` : `← [${e.edge_type}] ←`
                  return (
                    <Button
                      key={`${e.src_id}-${e.dst_id}-${i}`}
                      variant="outline"
                      size="xs"
                      className="font-mono text-xs font-normal"
                      onClick={() => setSelectedId(other)}
                    >
                      {arrow} {other}
                    </Button>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
