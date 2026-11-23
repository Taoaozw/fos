import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent,
} from "react"
import { debounce, useQueryStates } from "nuqs"
import {
  IconArrowUpRight,
  IconCrosshair,
  IconFocusCentered,
  IconMap,
  IconMaximize,
  IconMinimize,
  IconMinus,
  IconPlus,
  IconSearch,
  IconX,
} from "@tabler/icons-react"
import { StatusPill } from "@/components/shared/status-pill"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Kbd } from "@/components/ui/kbd"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  DRAG_THRESHOLD_PX,
  MAX_ZOOM,
  MIN_ZOOM,
  ZOOM_STEP_FACTOR,
  computeMinimapGeometry,
  computePinchViewport,
  computeWheelAction,
  createViewportScheduler,
  fitBoundsToViewport,
  focusWorldPoint,
  panBy,
  zoomAtPoint,
  type GestureMode,
  type Point,
  type Size,
  type Viewport,
  type ViewportScheduler,
} from "@/lib/graph-viewport"
import {
  graphFilterParsers,
  type GraphViewMode,
} from "@/lib/url-state"
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

function isEditableElement(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT"
}

const NODE_W = 224
const NODE_H = 56
const COL_GAP = 48
const ROW_GAP = 16
const PAD_X = 36
const HEADER_H = 52
const RIGHT_ARC_PAD = 64
const MINIMAP_W = 188
const MINIMAP_H = 118
const INITIAL_VIEWPORT: Viewport = { x: 16, y: 12, zoom: 0.9 }

export function GraphPanel({ dashboard, onJumpToCard, themeVersion }: GraphPanelProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [graphFilters, setGraphFilters] = useQueryStates(graphFilterParsers)
  const { gscope: scopeFilter, gmode: viewMode, ghistory: includeHistory, gq: search } = graphFilters
  const [palette, setPalette] = useState<Palette>(() => readThemePalette())

  const [viewport, setViewport] = useState<Viewport>(INITIAL_VIEWPORT)
  const [viewportSize, setViewportSize] = useState<Size>({ width: 0, height: 0 })
  const [isPanning, setIsPanning] = useState(false)
  const [spacePressed, setSpacePressed] = useState(false)
  const [showMinimap, setShowMinimap] = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)

  const viewportRef = useRef<HTMLDivElement | null>(null)
  const minimapRef = useRef<SVGSVGElement | null>(null)
  const viewportStateRef = useRef<Viewport>(INITIAL_VIEWPORT)
  const lastCommittedRef = useRef<Viewport>(INITIAL_VIEWPORT)
  const viewportSizeRef = useRef<Size>({ width: 0, height: 0 })
  const schedulerRef = useRef<ViewportScheduler | null>(null)

  const gestureModeRef = useRef<GestureMode>("idle")
  const activePointersRef = useRef<Map<number, Point>>(new Map())
  const dragOriginRef = useRef<{
    pointerId: number
    startClientX: number
    startClientY: number
    startViewport: Viewport
    downNodeId: string | null
  } | null>(null)
  const minimapDragPointerRef = useRef<number | null>(null)
  const suppressNextClickRef = useRef(false)
  const spacePressedRef = useRef(false)
  const lastAutoFitKeyRef = useRef<string | null>(null)

  if (!schedulerRef.current) {
    schedulerRef.current = createViewportScheduler((next) => {
      lastCommittedRef.current = next
      setViewport(next)
    })
  }

  const commitViewportDirect = useCallback((next: Viewport) => {
    viewportStateRef.current = next
    lastCommittedRef.current = next
    schedulerRef.current?.commitDirect(next)
  }, [])

  const scheduleViewport = useCallback((next: Viewport) => {
    viewportStateRef.current = next
    schedulerRef.current?.schedule(next)
  }, [])

  const cancelPendingViewport = useCallback(() => {
    schedulerRef.current?.cancel()
    viewportStateRef.current = lastCommittedRef.current
  }, [])

  useEffect(() => {
    return () => {
      schedulerRef.current?.cancel()
    }
  }, [])

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
            x.status !== "active"
              ? 4
              : x.type === "principle"
                ? 0
                : x.compat?.pattern === "code_exemplar"
                  ? 1
                  : x.severity === "P0"
                    ? 2
                    : 3
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

    const width = Math.max(760, PAD_X * 2 + scopes.length * colW - COL_GAP + RIGHT_ARC_PAD)
    const height = Math.max(420, HEADER_H + maxRows * (NODE_H + ROW_GAP) + 48)
    const visibleEdges = semanticEdges.filter((e) => nodeSet.has(e.src_id) && nodeSet.has(e.dst_id))
    return { width, height, positions, lanes, edges: visibleEdges }
  }, [filteredNodes, semanticEdges])

  const getViewportSize = useCallback((): Size => {
    const el = viewportRef.current
    if (el && el.clientWidth > 0 && el.clientHeight > 0) {
      return { width: el.clientWidth, height: el.clientHeight }
    }
    if (viewportSizeRef.current.width > 0 && viewportSizeRef.current.height > 0) {
      return viewportSizeRef.current
    }
    return { width: 960, height: 540 }
  }, [])

  const fitToView = useCallback(() => {
    const size = getViewportSize()
    commitViewportDirect(
      fitBoundsToViewport({ width: layout.width, height: layout.height }, size, 28, 1.0)
    )
  }, [commitViewportDirect, getViewportSize, layout.height, layout.width])

  const zoomByCenter = useCallback(
    (factor: number) => {
      const size = getViewportSize()
      const current = viewportStateRef.current
      commitViewportDirect(
        zoomAtPoint(current, { x: size.width / 2, y: size.height / 2 }, current.zoom * factor)
      )
    },
    [commitViewportDirect, getViewportSize]
  )

  const resetToActualSize = useCallback(() => {
    const size = getViewportSize()
    const current = viewportStateRef.current
    commitViewportDirect(zoomAtPoint(current, { x: size.width / 2, y: size.height / 2 }, 1))
  }, [commitViewportDirect, getViewportSize])

  const focusNode = useCallback(
    (nodeId: string, minZoom = 0.95) => {
      const pos = layout.positions[nodeId]
      if (!pos) return
      const size = getViewportSize()
      const targetZoom = Math.min(1.4, Math.max(minZoom, viewportStateRef.current.zoom))
      commitViewportDirect(focusWorldPoint({ x: pos.x, y: pos.y }, size, targetZoom))
    },
    [commitViewportDirect, getViewportSize, layout.positions]
  )

  // Track canvas container size changes (window resize / fullscreen toggle)
  useEffect(() => {
    const el = viewportRef.current
    if (!el) return

    const updateSize = () => {
      const next: Size = { width: el.clientWidth, height: el.clientHeight }
      if (next.width <= 0 || next.height <= 0) return
      const prev = viewportSizeRef.current
      if (prev.width === next.width && prev.height === next.height) return
      viewportSizeRef.current = next
      setViewportSize(next)
      if (prev.width > 0 && prev.height > 0) {
        commitViewportDirect(
          panBy(viewportStateRef.current, (next.width - prev.width) / 2, (next.height - prev.height) / 2)
        )
      }
    }

    updateSize()
    if (typeof ResizeObserver === "undefined") return
    const ro = new ResizeObserver(updateSize)
    ro.observe(el)
    return () => ro.disconnect()
  }, [commitViewportDirect])

  // Auto-fit once on initial load and whenever active filters change
  const filterKey = `${scopeFilter}|${viewMode}|${includeHistory}|${search.trim()}`
  useEffect(() => {
    if (viewportSize.width <= 0 || viewportSize.height <= 0 || filteredNodes.length === 0) return
    if (lastAutoFitKeyRef.current === filterKey) return
    lastAutoFitKeyRef.current = filterKey
    commitViewportDirect(
      fitBoundsToViewport({ width: layout.width, height: layout.height }, viewportSize, 28, 1.0)
    )
  }, [
    commitViewportDirect,
    filterKey,
    filteredNodes.length,
    layout.height,
    layout.width,
    viewportSize,
  ])

  // Non-passive native wheel listener so zoom/pinch never triggers browser page zoom or scroll
  useEffect(() => {
    const el = viewportRef.current
    if (!el) return

    const onWheel = (e: globalThis.WheelEvent) => {
      if ((e.target as HTMLElement | null)?.closest("[data-canvas-overlay='true']")) return
      const action = computeWheelAction(e, el.clientHeight || 600)
      if (action.type === "none") return
      e.preventDefault()

      const rect = el.getBoundingClientRect()
      const anchor = { x: e.clientX - rect.left, y: e.clientY - rect.top }
      const current = viewportStateRef.current
      if (action.type === "zoom") {
        commitViewportDirect(zoomAtPoint(current, anchor, current.zoom * action.factor))
      } else {
        commitViewportDirect(panBy(current, action.dx, action.dy))
      }
    }

    const onGesture = (e: Event) => {
      e.preventDefault()
    }

    el.addEventListener("wheel", onWheel, { passive: false })
    el.addEventListener("gesturestart", onGesture, { passive: false })
    el.addEventListener("gesturechange", onGesture, { passive: false })
    return () => {
      el.removeEventListener("wheel", onWheel)
      el.removeEventListener("gesturestart", onGesture)
      el.removeEventListener("gesturechange", onGesture)
    }
  }, [commitViewportDirect])

  // Keyboard shortcuts for canvas navigation
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const el = viewportRef.current
      if (!el || el.offsetParent === null) return
      if (isEditableElement(e.target)) return

      if (e.code === "Space" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const targetEl = e.target as HTMLElement | null
        const insideCanvas = Boolean(targetEl && el.contains(targetEl))
        const onBody = targetEl === document.body
        if (insideCanvas || onBody || isFullscreen) {
          e.preventDefault()
          if (!spacePressedRef.current) {
            spacePressedRef.current = true
            setSpacePressed(true)
          }
        }
        return
      }

      if (e.ctrlKey || e.metaKey || e.altKey) return

      if (e.key === "Escape") {
        if (isFullscreen) {
          e.preventDefault()
          setIsFullscreen(false)
        } else if (selectedId) {
          setSelectedId(null)
        }
        return
      }

      if (e.key === "+" || e.key === "=") {
        e.preventDefault()
        zoomByCenter(ZOOM_STEP_FACTOR)
        return
      }

      if (e.key === "-" || e.key === "_") {
        e.preventDefault()
        zoomByCenter(1 / ZOOM_STEP_FACTOR)
        return
      }

      if (e.key === "0" || e.key === "f" || e.key === "F") {
        e.preventDefault()
        fitToView()
        return
      }

      if (e.key === "1") {
        e.preventDefault()
        resetToActualSize()
        return
      }

      if (e.key === "m" || e.key === "M") {
        e.preventDefault()
        setShowMinimap((prev) => !prev)
        return
      }

      // Arrow keys pan when canvas has focus or is fullscreen
      const canvasActive = document.activeElement === el || isFullscreen
      if (!canvasActive) return
      const step = e.shiftKey ? 200 : 80
      if (e.key === "ArrowLeft") {
        e.preventDefault()
        commitViewportDirect(panBy(viewportStateRef.current, step, 0))
      } else if (e.key === "ArrowRight") {
        e.preventDefault()
        commitViewportDirect(panBy(viewportStateRef.current, -step, 0))
      } else if (e.key === "ArrowUp") {
        e.preventDefault()
        commitViewportDirect(panBy(viewportStateRef.current, 0, step))
      } else if (e.key === "ArrowDown") {
        e.preventDefault()
        commitViewportDirect(panBy(viewportStateRef.current, 0, -step))
      }
    }

    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === "Space" && spacePressedRef.current) {
        spacePressedRef.current = false
        setSpacePressed(false)
      }
    }

    const onBlur = () => {
      if (spacePressedRef.current) {
        spacePressedRef.current = false
        setSpacePressed(false)
      }
    }

    window.addEventListener("keydown", onKeyDown)
    window.addEventListener("keyup", onKeyUp)
    window.addEventListener("blur", onBlur)
    return () => {
      window.removeEventListener("keydown", onKeyDown)
      window.removeEventListener("keyup", onKeyUp)
      window.removeEventListener("blur", onBlur)
    }
  }, [commitViewportDirect, fitToView, isFullscreen, resetToActualSize, selectedId, zoomByCenter])

  // Lock page scroll while fullscreen overlay is active
  useEffect(() => {
    if (!isFullscreen) return
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
    return () => {
      document.body.style.overflow = prevOverflow
    }
  }, [isFullscreen])

  // Pointer gestures: drag anywhere (with click threshold), middle-drag, Space+drag, 2-finger touch pinch
  const getLocalPoint = (e: { clientX: number; clientY: number }): Point => {
    const rect = viewportRef.current?.getBoundingClientRect()
    if (!rect) return { x: e.clientX, y: e.clientY }
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  const handlePointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement | null)?.closest("[data-canvas-overlay='true']")) return
    if (e.pointerType === "mouse" && e.button !== 0 && e.button !== 1) return

    const el = viewportRef.current
    if (!el) return

    if (document.activeElement !== el) {
      el.focus({ preventScroll: true })
    }

    const pt = getLocalPoint(e)
    activePointersRef.current.set(e.pointerId, pt)

    // Two active pointers -> touch pinch-to-zoom + pan
    if (activePointersRef.current.size === 2) {
      gestureModeRef.current = "pinching"
      dragOriginRef.current = null
      suppressNextClickRef.current = true
      setIsPanning(true)
      try {
        el.setPointerCapture(e.pointerId)
      } catch {
        // Ignore capture errors on synthetic pointers
      }
      return
    }

    const nodeEl = (e.target as Element | null)?.closest("[data-graph-node-id]")
    const downNodeId = nodeEl?.getAttribute("data-graph-node-id") ?? null
    const immediatePan = e.button === 1 || spacePressedRef.current

    if (immediatePan) {
      e.preventDefault()
      gestureModeRef.current = "panning"
      suppressNextClickRef.current = true
      setIsPanning(true)
      try {
        el.setPointerCapture(e.pointerId)
      } catch {
        // Ignore capture errors
      }
    } else {
      gestureModeRef.current = "pending-pan"
      suppressNextClickRef.current = false
    }

    dragOriginRef.current = {
      pointerId: e.pointerId,
      startClientX: e.clientX,
      startClientY: e.clientY,
      startViewport: viewportStateRef.current,
      downNodeId,
    }
  }

  const handlePointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const el = viewportRef.current
    if (!el) return

    if (gestureModeRef.current === "pinching" && activePointersRef.current.has(e.pointerId)) {
      const entries = [...activePointersRef.current.entries()]
      if (entries.length >= 2) {
        const [id1, p1Prev] = entries[0]
        const [id2, p2Prev] = entries[1]
        const nextPt = getLocalPoint(e)
        activePointersRef.current.set(e.pointerId, nextPt)
        const p1Next = id1 === e.pointerId ? nextPt : p1Prev
        const p2Next = id2 === e.pointerId ? nextPt : p2Prev
        scheduleViewport(
          computePinchViewport(viewportStateRef.current, p1Prev, p2Prev, p1Next, p2Next)
        )
      }
      return
    }

    const origin = dragOriginRef.current
    if (!origin || origin.pointerId !== e.pointerId) return

    const dx = e.clientX - origin.startClientX
    const dy = e.clientY - origin.startClientY

    if (gestureModeRef.current === "pending-pan") {
      if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return
      gestureModeRef.current = "panning"
      suppressNextClickRef.current = true
      setIsPanning(true)
      try {
        el.setPointerCapture(e.pointerId)
      } catch {
        // Ignore capture errors
      }
    }

    if (gestureModeRef.current === "panning") {
      scheduleViewport(panBy(origin.startViewport, dx, dy))
    }
  }

  const finishPointer = (e: PointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const el = viewportRef.current
    const prevMode = gestureModeRef.current
    const origin = dragOriginRef.current

    activePointersRef.current.delete(e.pointerId)

    if (el?.hasPointerCapture(e.pointerId)) {
      try {
        el.releasePointerCapture(e.pointerId)
      } catch {
        // Ignore release errors
      }
    }

    if (cancelled) {
      // LSN-059: Cancel any queued rAF on pointercancel so stale frames never overwrite viewport
      cancelPendingViewport()
      activePointersRef.current.clear()
      dragOriginRef.current = null
      gestureModeRef.current = "idle"
      setIsPanning(false)
      return
    }

    // If lifting one finger during a 2-finger pinch, end pinch cleanly
    if (prevMode === "pinching") {
      if (activePointersRef.current.size === 0) {
        gestureModeRef.current = "idle"
        setIsPanning(false)
      }
      return
    }

    if (origin && origin.pointerId === e.pointerId) {
      if (prevMode === "pending-pan") {
        // Pointer did not cross drag threshold -> treat as click
        if (origin.downNodeId) {
          setSelectedId(origin.downNodeId)
        } else {
          setSelectedId(null)
        }
      }
      dragOriginRef.current = null
      gestureModeRef.current = "idle"
      setIsPanning(false)
    }
  }

  const handleDoubleClick = (e: MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement | null)?.closest("[data-canvas-overlay='true']")) return
    if (suppressNextClickRef.current) {
      suppressNextClickRef.current = false
      return
    }

    const nodeEl = (e.target as Element | null)?.closest("[data-graph-node-id]")
    const nodeId = nodeEl?.getAttribute("data-graph-node-id")
    if (nodeId && layout.positions[nodeId]) {
      setSelectedId(nodeId)
      focusNode(nodeId, 1.05)
      return
    }

    const current = viewportStateRef.current
    if (current.zoom >= 1.9) {
      fitToView()
      return
    }
    const anchor = getLocalPoint(e)
    commitViewportDirect(zoomAtPoint(current, anchor, current.zoom * 1.5))
  }

  // Minimap geometry & scrub interaction
  const minimapGeo = useMemo(
    () =>
      computeMinimapGeometry(
        { width: layout.width, height: layout.height },
        viewport,
        viewportSize.width > 0 ? viewportSize : { width: 960, height: 540 },
        { width: MINIMAP_W, height: MINIMAP_H },
        6
      ),
    [layout.height, layout.width, viewport, viewportSize]
  )

  const panFromMinimapEvent = (e: PointerEvent<SVGSVGElement>) => {
    const svg = minimapRef.current
    if (!svg) return
    const rect = svg.getBoundingClientRect()
    const worldPt = minimapGeo.toWorld({
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    })
    commitViewportDirect(
      focusWorldPoint(worldPt, getViewportSize(), viewportStateRef.current.zoom)
    )
  }

  const handleMinimapPointerDown = (e: PointerEvent<SVGSVGElement>) => {
    e.stopPropagation()
    minimapDragPointerRef.current = e.pointerId
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      // Ignore capture errors
    }
    panFromMinimapEvent(e)
  }

  const handleMinimapPointerMove = (e: PointerEvent<SVGSVGElement>) => {
    if (minimapDragPointerRef.current !== e.pointerId) return
    e.stopPropagation()
    panFromMinimapEvent(e)
  }

  const handleMinimapPointerEnd = (e: PointerEvent<SVGSVGElement>, cancelled: boolean) => {
    if (minimapDragPointerRef.current !== e.pointerId) return
    e.stopPropagation()
    minimapDragPointerRef.current = null
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      try {
        e.currentTarget.releasePointerCapture(e.pointerId)
      } catch {
        // Ignore release errors
      }
    }
    if (cancelled) {
      cancelPendingViewport()
    }
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

  const zoomPct = Math.round(viewport.zoom * 100)
  const gridOpacity =
    viewport.zoom < 0.3 ? 0 : Math.min(0.32, (viewport.zoom - 0.3) * 0.55)
  const dotRadius = Math.min(1.6, Math.max(0.85, 1.15 / Math.sqrt(Math.max(0.3, viewport.zoom))))

  const inspectorCard = selectedNode && (
    <div
      data-canvas-overlay="true"
      className="surface-card pointer-events-auto absolute top-3 right-3 z-20 flex max-h-[calc(100%-156px)] w-[min(420px,calc(100%-24px))] flex-col gap-2.5 overflow-y-auto bg-card/95 px-4 py-3.5 text-sm shadow-lg backdrop-blur-md"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <Badge variant="outline" className="rounded-md font-mono text-xs font-medium">
            {selectedNode.id}
          </Badge>
          <StatusPill
            tone={selectedNode.severity === "P0" ? "danger" : selectedNode.severity === "P1" ? "warning" : "neutral"}
            dot={selectedNode.severity === "P0"}
            mono
            className="py-0 text-[11px]"
          >
            {selectedNode.severity}
          </StatusPill>
          <StatusPill tone="purple" dot={false} mono className="py-0 text-[11px]">
            {selectedNode.type}
          </StatusPill>
          <StatusPill tone="info" dot={false} mono className="py-0 text-[11px]">
            {selectedNode.scope}
          </StatusPill>
          {selectedNode.compat?.pattern === "code_exemplar" && (
            <StatusPill tone="neutral" dot={false} mono className="py-0 text-[11px]">
              pattern:{selectedNode.compat.lang ?? "code"}
            </StatusPill>
          )}
          {selectedNode.freshness && selectedNode.freshness !== "untracked" && (
            <StatusPill
              tone={
                selectedNode.freshness === "broken_ref"
                  ? "danger"
                  : selectedNode.freshness === "drifted"
                    ? "warning"
                    : "success"
              }
              mono
              className="py-0 text-[11px]"
            >
              {selectedNode.freshness}
            </StatusPill>
          )}
        </div>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="关闭节点详情"
          className="shrink-0"
          onClick={() => setSelectedId(null)}
        >
          <IconX className="size-3.5" />
        </Button>
      </div>

      <div className="text-[14px] font-semibold leading-snug text-foreground">
        {selectedNode.rule}
      </div>

      {selectedNode.source_ref && (
        <div className="font-mono text-xs break-all text-muted-foreground">
          {selectedNode.source_ref}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5">
        {layout.positions[selectedNode.id] && (
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs font-normal"
            onClick={() => focusNode(selectedNode.id, 1.0)}
          >
            <IconCrosshair className="size-3.5" />
            定位
          </Button>
        )}
        <Button size="sm" className="h-7 text-xs" onClick={() => onJumpToCard(selectedNode.id)}>
          查看 L2 证据与操作
          <IconArrowUpRight className="size-3.5" />
        </Button>
      </div>

      {selectedEdges.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-border/80 pt-2 text-xs">
          <span className="text-muted-foreground">关联边 ({selectedEdges.length})：</span>
          {selectedEdges.map((e, i) => {
            const other = e.src_id === selectedNode.id ? e.dst_id : e.src_id
            const arrow =
              e.src_id === selectedNode.id ? `→ [${e.edge_type}] →` : `← [${e.edge_type}] ←`
            return (
              <Button
                key={`${e.src_id}-${e.dst_id}-${i}`}
                variant="outline"
                size="xs"
                className="font-mono text-xs font-normal"
                onClick={() => {
                  setSelectedId(other)
                  focusNode(other, 0.95)
                }}
              >
                {arrow} {other}
              </Button>
            )
          })}
        </div>
      )}
    </div>
  )

  return (
    <div className="flex min-w-0 w-full flex-col gap-3.5">
      <div className="surface-card flex min-w-0 flex-col gap-3 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-foreground">双时态知识图谱</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              拖拽平移 · 滚轮 / 双指捏合缩放 · 双击聚焦 · 悬停高亮 1-hop 演进与冲突邻域（显示{" "}
              {filteredNodes.length} 节点 / {layout.edges.length} 语义边）
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
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/70 pt-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative w-52">
              <IconSearch className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => {
                  const next = e.target.value
                  void setGraphFilters(
                    { gq: next },
                    { limitUrlUpdates: next === "" ? undefined : debounce(250) }
                  )
                }}
                placeholder="搜索节点 / 规则..."
                className="h-8 pl-8 text-xs"
              />
            </div>

            <Select
              value={scopeFilter}
              onValueChange={(v) => void setGraphFilters({ gscope: v ?? "__all__" })}
            >
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

            <Button
              variant={viewMode === "connected" ? "secondary" : "outline"}
              size="sm"
              className="font-normal text-xs"
              onClick={() =>
                void setGraphFilters({
                  gmode: (viewMode === "all" ? "connected" : "all") as GraphViewMode,
                })
              }
            >
              {viewMode === "connected" ? "仅关系子图" : "全部节点"}
            </Button>

            <Button
              variant={includeHistory ? "secondary" : "outline"}
              size="sm"
              className="font-normal text-xs"
              onClick={() => void setGraphFilters({ ghistory: !includeHistory })}
            >
              {includeHistory ? "含历史演进" : "仅活跃节点"}
            </Button>
          </div>

          <div className="hidden items-center gap-1.5 text-[11px] text-muted-foreground xl:flex">
            <Kbd>Space</Kbd>
            <span>平移</span>
            <span className="mx-0.5 text-border">·</span>
            <Kbd>+</Kbd>
            <Kbd>-</Kbd>
            <span>缩放</span>
            <span className="mx-0.5 text-border">·</span>
            <Kbd>0</Kbd>
            <span>适应</span>
            <span className="mx-0.5 text-border">·</span>
            <Kbd>1</Kbd>
            <span>100%</span>
          </div>
        </div>
      </div>

      <div
        className={
          isFullscreen
            ? "fixed inset-0 z-50 flex flex-col bg-background p-3"
            : "flex min-w-0 w-full flex-col gap-3"
        }
      >
        <div
          ref={viewportRef}
          tabIndex={0}
          role="application"
          aria-label="双时态知识图谱画布"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={(e) => finishPointer(e, false)}
          onPointerCancel={(e) => finishPointer(e, true)}
          onDoubleClick={handleDoubleClick}
          className={`surface-card relative min-w-0 w-full touch-none overflow-hidden outline-none select-none focus-visible:ring-2 focus-visible:ring-ring/40 ${
            isFullscreen ? "flex-1" : "h-[calc(100dvh-204px)] min-h-[500px]"
          } ${isPanning ? "cursor-grabbing" : "cursor-grab"}`}
        >
          {filteredNodes.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              当前筛选条件下没有可展示的图谱节点（可切换为「全部节点」或清空搜索词）。
            </div>
          ) : (
            <svg className="block h-full w-full">
              <defs>
                <pattern
                  id="fos-canvas-grid"
                  width={24}
                  height={24}
                  patternUnits="userSpaceOnUse"
                  patternTransform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.zoom})`}
                >
                  <circle cx={1} cy={1} r={dotRadius} fill={palette.muted} opacity={gridOpacity} />
                </pattern>
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

              {/* World-locked dot grid background */}
              {gridOpacity > 0 && (
                <rect width="100%" height="100%" fill="url(#fos-canvas-grid)" />
              )}

              <g transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.zoom})`}>
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
                  const startX = sameCol
                    ? a.x + NODE_W / 2
                    : a.x < b.x
                      ? a.x + NODE_W / 2
                      : a.x - NODE_W / 2
                  const endX = sameCol
                    ? b.x + NODE_W / 2
                    : a.x < b.x
                      ? b.x - NODE_W / 2
                      : b.x + NODE_W / 2
                  const bend = sameCol ? 54 + (idx % 3) * 16 : 0
                  const mx = (startX + endX) / 2 + bend
                  const my = (a.y + b.y) / 2 - (sameCol ? 0 : 18)
                  const showEdgeLabel = viewport.zoom >= 0.52 || (Boolean(focusId) && isIncident)
                  return (
                    <g
                      key={`${e.src_id}-${e.dst_id}-${e.edge_type}-${idx}`}
                      opacity={isIncident ? 0.92 : 0.14}
                    >
                      <path
                        d={`M ${startX} ${a.y} Q ${mx} ${my} ${endX} ${b.y}`}
                        fill="none"
                        stroke={edgeColor}
                        strokeWidth={isIncident && focusId ? 2.2 : 1.5}
                        strokeDasharray={e.edge_type === "CONTRADICTS" ? "5 3" : undefined}
                        markerEnd={edgeMarkerFor(e.edge_type)}
                      />
                      {showEdgeLabel && (
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
                      )}
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
                      data-graph-node-id={node.id}
                      onMouseEnter={() => {
                        if (!isPanning) setHoveredId(node.id)
                      }}
                      onMouseLeave={() => setHoveredId((prev) => (prev === node.id ? null : prev))}
                      className={spacePressed || isPanning ? "cursor-grabbing" : "cursor-pointer"}
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
              </g>
            </svg>
          )}

          {/* Fullscreen Top-Left Title Chip */}
          {isFullscreen && (
            <div
              data-canvas-overlay="true"
              className="absolute top-3 left-3 z-10 flex items-center gap-2 rounded-lg border border-border bg-card/92 px-3 py-1.5 text-xs shadow-sm backdrop-blur"
            >
              <span className="font-medium text-foreground">双时态知识图谱</span>
              <span className="text-border">·</span>
              <span className="font-mono text-muted-foreground">
                {filteredNodes.length} 节点 / {layout.edges.length} 边
              </span>
            </div>
          )}

          {/* Floating Canvas Control Dock (Bottom-Left) */}
          <div
            data-canvas-overlay="true"
            className="absolute bottom-3 left-3 z-10 flex items-center gap-1 rounded-lg border border-border bg-card/92 p-1 shadow-sm backdrop-blur"
          >
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="缩小"
                    disabled={viewport.zoom <= MIN_ZOOM + 1e-4}
                    onClick={() => zoomByCenter(1 / ZOOM_STEP_FACTOR)}
                  />
                }
              >
                <IconMinus className="size-3.5" />
              </TooltipTrigger>
              <TooltipContent>
                缩小 <Kbd>-</Kbd>
              </TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label="重置为 100%"
                    className="h-8 w-14 font-mono text-xs font-normal"
                    onClick={resetToActualSize}
                  />
                }
              >
                {zoomPct}%
              </TooltipTrigger>
              <TooltipContent>
                重置为 100% <Kbd>1</Kbd>
              </TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="放大"
                    disabled={viewport.zoom >= MAX_ZOOM - 1e-4}
                    onClick={() => zoomByCenter(ZOOM_STEP_FACTOR)}
                  />
                }
              >
                <IconPlus className="size-3.5" />
              </TooltipTrigger>
              <TooltipContent>
                放大 <Kbd>+</Kbd>
              </TooltipContent>
            </Tooltip>

            <div className="mx-0.5 h-4 w-px bg-border" />

            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label="适应画布"
                    className="h-8 px-2 text-xs font-normal"
                    onClick={fitToView}
                  />
                }
              >
                <IconFocusCentered className="size-3.5" />
                <span className="hidden sm:inline">适应</span>
              </TooltipTrigger>
              <TooltipContent>
                适应画布 <Kbd>0</Kbd> / <Kbd>F</Kbd>
              </TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant={showMinimap ? "secondary" : "ghost"}
                    size="icon-sm"
                    aria-label={showMinimap ? "隐藏小地图" : "显示小地图"}
                    onClick={() => setShowMinimap((prev) => !prev)}
                  />
                }
              >
                <IconMap className="size-3.5" />
              </TooltipTrigger>
              <TooltipContent>
                {showMinimap ? "隐藏小地图" : "显示小地图"} <Kbd>M</Kbd>
              </TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant={isFullscreen ? "secondary" : "ghost"}
                    size="icon-sm"
                    aria-label={isFullscreen ? "退出全屏" : "全屏画布"}
                    onClick={() => setIsFullscreen((prev) => !prev)}
                  />
                }
              >
                {isFullscreen ? (
                  <IconMinimize className="size-3.5" />
                ) : (
                  <IconMaximize className="size-3.5" />
                )}
              </TooltipTrigger>
              <TooltipContent>
                {isFullscreen ? (
                  <>
                    退出全屏 <Kbd>Esc</Kbd>
                  </>
                ) : (
                  "全屏查看画布"
                )}
              </TooltipContent>
            </Tooltip>
          </div>

          {/* Interactive Minimap Navigator (Bottom-Right) */}
          {showMinimap && filteredNodes.length > 0 && (
            <div
              data-canvas-overlay="true"
              className="absolute right-3 bottom-3 z-10 hidden overflow-hidden rounded-lg border border-border bg-card/92 shadow-sm backdrop-blur sm:block"
            >
              <svg
                ref={minimapRef}
                width={MINIMAP_W}
                height={MINIMAP_H}
                onPointerDown={handleMinimapPointerDown}
                onPointerMove={handleMinimapPointerMove}
                onPointerUp={(e) => handleMinimapPointerEnd(e, false)}
                onPointerCancel={(e) => handleMinimapPointerEnd(e, true)}
                className="block cursor-crosshair touch-none"
                aria-label="图谱小地图导航"
              >
                <g transform={`translate(${minimapGeo.offsetX} ${minimapGeo.offsetY})`}>
                  {layout.lanes.map((lane) => (
                    <rect
                      key={`mm-lane-${lane.scope}`}
                      x={lane.x * minimapGeo.scale}
                      y={14 * minimapGeo.scale}
                      width={Math.max(2, lane.w * minimapGeo.scale)}
                      height={Math.max(2, lane.h * minimapGeo.scale)}
                      rx={2}
                      fill={palette.mutedBg}
                      fillOpacity={0.7}
                      stroke={palette.border}
                      strokeWidth={0.6}
                    />
                  ))}
                  {Object.values(layout.positions).map(({ x, y, node }) => {
                    const isFocused = Boolean(focusNeighbors?.has(node.id))
                    return (
                      <rect
                        key={`mm-node-${node.id}`}
                        x={(x - NODE_W / 2) * minimapGeo.scale}
                        y={(y - NODE_H / 2) * minimapGeo.scale}
                        width={Math.max(2.5, NODE_W * minimapGeo.scale)}
                        height={Math.max(1.8, NODE_H * minimapGeo.scale)}
                        rx={0.8}
                        fill={strokeFor(node)}
                        opacity={focusNeighbors ? (isFocused ? 1 : 0.2) : 0.8}
                      />
                    )
                  })}
                </g>

                {/* Current Viewport Frame */}
                <rect
                  x={minimapGeo.viewRect.x}
                  y={minimapGeo.viewRect.y}
                  width={Math.max(8, minimapGeo.viewRect.width)}
                  height={Math.max(6, minimapGeo.viewRect.height)}
                  rx={2}
                  fill={palette.c1}
                  fillOpacity={0.14}
                  stroke={palette.c1}
                  strokeWidth={1.3}
                />
              </svg>
            </div>
          )}

          {/* Floating Selected Node Inspector */}
          {inspectorCard}
        </div>
      </div>
    </div>
  )
}
