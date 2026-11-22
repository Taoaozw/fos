import { useEffect, useMemo, useState } from "react"
import { IconArrowUpRight } from "@tabler/icons-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import type { CompiledNode, DashboardResponse } from "@/lib/types"

interface GraphPanelProps {
  dashboard: DashboardResponse
  onJumpToCard: (nodeId: string) => void
  themeVersion: number
}

interface Palette {
  bg: string
  card: string
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

export function GraphPanel({ dashboard, onJumpToCard, themeVersion }: GraphPanelProps) {
  const [selected, setSelected] = useState<CompiledNode | null>(null)
  const [palette, setPalette] = useState<Palette>(() => readThemePalette())

  useEffect(() => {
    setPalette(readThemePalette())
  }, [themeVersion])

  const layout = useMemo(() => {
    const nodes = dashboard.nodes
    const scopes = [...new Set(nodes.map((n) => n.scope))].sort((a, b) =>
      a === "global" ? -1 : b === "global" ? 1 : a.localeCompare(b)
    )
    const width = 1080
    const height = Math.max(460, 120 + Math.max(1, ...scopes.map((s) => nodes.filter((n) => n.scope === s).length)) * 78)
    const colW = Math.max(240, (width - 80) / Math.max(1, scopes.length))

    const positions: Record<string, { x: number; y: number; node: CompiledNode }> = {}
    const scopeHeaders: Array<{ scope: string; x: number; count: number }> = []

    scopes.forEach((sc, ci) => {
      const cx = 40 + colW * ci + colW / 2
      const group = nodes.filter((n) => n.scope === sc)
      scopeHeaders.push({ scope: sc, x: cx, count: group.length })
      group.forEach((n, ri) => {
        positions[n.id] = { x: cx, y: 86 + ri * 74, node: n }
      })
    })

    const edges = dashboard.edges.filter((e) => !e.dst_id.startsWith("scope:"))
    return { width, height, positions, scopeHeaders, edges }
  }, [dashboard.nodes, dashboard.edges])

  const strokeFor = (n: CompiledNode) => {
    if (n.status === "archived") return palette.muted
    if (n.status === "superseded") return palette.c3
    if (n.type === "principle") return palette.c4
    return palette.c2
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle className="text-sm font-medium">双时态知识图谱</CardTitle>
            <CardDescription>按作用域分组展示活跃、已纠偏与已归档节点及其演进边</CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="outline" className="rounded-md text-xs">
              principle
            </Badge>
            <Badge variant="outline" className="rounded-md text-xs">
              active
            </Badge>
            <Badge variant="outline" className="rounded-md text-xs">
              superseded
            </Badge>
            <Badge variant="outline" className="rounded-md text-xs">
              archived
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="overflow-x-auto rounded-lg border border-border bg-muted/20">
          <svg
            viewBox={`0 0 ${layout.width} ${layout.height}`}
            className="h-[480px] w-full min-w-[720px]"
          >
            <defs>
              <marker
                id="fos-arrow"
                viewBox="0 0 10 10"
                refX="10"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 1 L 10 5 L 0 9 z" fill={palette.c1} />
              </marker>
            </defs>

            {layout.scopeHeaders.map((h) => (
              <text
                key={h.scope}
                x={h.x}
                y={34}
                textAnchor="middle"
                fill={palette.muted}
                fontSize={12}
                fontFamily="monospace"
              >
                scope: {h.scope} ({h.count})
              </text>
            ))}

            {layout.edges.map((e, idx) => {
              const a = layout.positions[e.src_id]
              const b = layout.positions[e.dst_id]
              if (!a || !b) return null
              const edgeColor =
                e.edge_type === "SUPERSEDES"
                  ? palette.c3
                  : e.edge_type === "CONTRADICTS"
                    ? palette.c5
                    : palette.c1
              const mx = (a.x + b.x) / 2 + 44
              const my = (a.y + b.y) / 2
              return (
                <g key={`${e.src_id}-${e.dst_id}-${idx}`}>
                  <path
                    d={`M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`}
                    fill="none"
                    stroke={edgeColor}
                    strokeWidth={1.6}
                    markerEnd="url(#fos-arrow)"
                    opacity={0.85}
                  />
                  <text
                    x={mx}
                    y={my}
                    fill={edgeColor}
                    fontSize={10}
                    fontFamily="monospace"
                  >
                    {e.edge_type}
                  </text>
                </g>
              )
            })}

            {Object.values(layout.positions).map(({ x, y, node }) => {
              const stroke = strokeFor(node)
              const label = node.rule.length > 18 ? `${node.rule.slice(0, 18)}…` : node.rule
              const isSelected = selected?.id === node.id
              return (
                <g
                  key={node.id}
                  onClick={() => setSelected(node)}
                  className="cursor-pointer"
                  opacity={node.status === "archived" ? 0.55 : 1}
                >
                  <rect
                    x={x - 104}
                    y={y - 24}
                    width={208}
                    height={48}
                    rx={8}
                    fill={palette.card}
                    stroke={stroke}
                    strokeWidth={isSelected ? 2.4 : 1.4}
                  />
                  <text
                    x={x - 94}
                    y={y - 6}
                    fill={palette.fg}
                    fontSize={11}
                    fontFamily="monospace"
                  >
                    {node.id} · {node.severity} · {node.status}
                  </text>
                  <text x={x - 94} y={y + 13} fill={palette.muted} fontSize={11}>
                    {label}
                  </text>
                </g>
              )
            })}
          </svg>
        </div>

        {selected && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3 text-sm">
            <div className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="outline" className="rounded-md font-mono text-xs">
                  {selected.id}
                </Badge>
                <Badge variant="secondary" className="rounded-md text-xs">
                  {selected.type}
                </Badge>
                <Badge variant="outline" className="rounded-md text-xs">
                  {selected.scope}
                </Badge>
                <Badge variant="outline" className="rounded-md text-xs">
                  {selected.status}
                </Badge>
              </div>
              <div className="text-foreground">{selected.rule}</div>
            </div>
            <Button variant="outline" size="sm" onClick={() => onJumpToCard(selected.id)}>
              查看 L2 证据与操作
              <IconArrowUpRight className="size-3.5" />
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
