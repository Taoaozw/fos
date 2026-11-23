export interface Viewport {
  x: number
  y: number
  zoom: number
}

export interface Point {
  x: number
  y: number
}

export interface Size {
  width: number
  height: number
}

export type GestureMode = "idle" | "pending-pan" | "panning" | "pinching"

export type WheelAction =
  | { type: "zoom"; factor: number }
  | { type: "pan"; dx: number; dy: number }
  | { type: "none" }

export interface WheelLike {
  deltaX: number
  deltaY: number
  deltaMode: number
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
}

export const MIN_ZOOM = 0.1
export const MAX_ZOOM = 3.0
export const ZOOM_STEP_FACTOR = 1.25
export const DRAG_THRESHOLD_PX = 4

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function clampZoom(zoom: number, min = MIN_ZOOM, max = MAX_ZOOM): number {
  if (!Number.isFinite(zoom)) return 1
  return clamp(zoom, min, max)
}

export function panBy(viewport: Viewport, dx: number, dy: number): Viewport {
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) return viewport
  if (dx === 0 && dy === 0) return viewport
  return {
    x: viewport.x + dx,
    y: viewport.y + dy,
    zoom: viewport.zoom,
  }
}

/**
 * Zoom to `targetZoom` while keeping the world point under `anchor`
 * pinned to the same screen coordinates inside the viewport.
 */
export function zoomAtPoint(
  viewport: Viewport,
  anchor: Point,
  targetZoom: number,
  min = MIN_ZOOM,
  max = MAX_ZOOM
): Viewport {
  const nextZoom = clampZoom(targetZoom, min, max)
  if (Math.abs(nextZoom - viewport.zoom) < 1e-6) return viewport
  const worldX = (anchor.x - viewport.x) / viewport.zoom
  const worldY = (anchor.y - viewport.y) / viewport.zoom
  return {
    x: anchor.x - worldX * nextZoom,
    y: anchor.y - worldY * nextZoom,
    zoom: nextZoom,
  }
}

export function fitBoundsToViewport(
  bounds: Size,
  viewportSize: Size,
  padding = 28,
  maxZoom = 1.0
): Viewport {
  if (viewportSize.width <= 0 || viewportSize.height <= 0) {
    return { x: 16, y: 12, zoom: 0.9 }
  }
  const availW = Math.max(1, viewportSize.width - padding * 2)
  const availH = Math.max(1, viewportSize.height - padding * 2)
  const contentW = Math.max(1, bounds.width)
  const contentH = Math.max(1, bounds.height)
  const zoom = clampZoom(Math.min(availW / contentW, availH / contentH), MIN_ZOOM, maxZoom)
  const scaledW = contentW * zoom
  const scaledH = contentH * zoom
  return {
    x: scaledW <= availW ? (viewportSize.width - scaledW) / 2 : padding,
    y: scaledH <= availH ? (viewportSize.height - scaledH) / 2 : padding,
    zoom,
  }
}

export function focusWorldPoint(
  worldPoint: Point,
  viewportSize: Size,
  targetZoom: number
): Viewport {
  const zoom = clampZoom(targetZoom)
  return {
    x: viewportSize.width / 2 - worldPoint.x * zoom,
    y: viewportSize.height / 2 - worldPoint.y * zoom,
    zoom,
  }
}

export function computeWheelAction(e: WheelLike, viewportHeight = 600): WheelAction {
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? Math.max(200, viewportHeight) : 1
  const dx = e.deltaX * unit
  const dy = e.deltaY * unit

  // Pinch-to-zoom on macOS/Windows precision trackpads emits ctrlKey=true wheel events;
  // Ctrl/Cmd + wheel also lands here.
  if (e.ctrlKey || e.metaKey) {
    if (dy === 0) return { type: "none" }
    const clamped = clamp(dy, -36, 36)
    return { type: "zoom", factor: Math.exp(-clamped * 0.007) }
  }

  // Shift + wheel pans horizontally (or along both axes if deltaX is also present).
  if (e.shiftKey) {
    if (dx === 0 && dy === 0) return { type: "none" }
    return {
      type: "pan",
      dx: -(dx !== 0 ? dx : dy),
      dy: dx !== 0 ? -dy : 0,
    }
  }

  // Plain vertical wheel zooms smoothly around the cursor.
  if (dy !== 0) {
    const clamped = clamp(dy, -120, 120)
    return { type: "zoom", factor: Math.exp(-clamped * 0.0022) }
  }

  // Pure horizontal wheel / tilt-wheel pans horizontally.
  if (dx !== 0) {
    return { type: "pan", dx: -dx, dy: 0 }
  }

  return { type: "none" }
}

/**
 * Compute next viewport from two-finger touch pinch/pan motion relative to the
 * previous touch pair.
 */
export function computePinchViewport(
  viewport: Viewport,
  p1Prev: Point,
  p2Prev: Point,
  p1Next: Point,
  p2Next: Point
): Viewport {
  const midPrev = { x: (p1Prev.x + p2Prev.x) / 2, y: (p1Prev.y + p2Prev.y) / 2 }
  const midNext = { x: (p1Next.x + p2Next.x) / 2, y: (p1Next.y + p2Next.y) / 2 }
  const distPrev = Math.hypot(p2Prev.x - p1Prev.x, p2Prev.y - p1Prev.y)
  const distNext = Math.hypot(p2Next.x - p1Next.x, p2Next.y - p1Next.y)

  if (distPrev < 1) {
    return panBy(viewport, midNext.x - midPrev.x, midNext.y - midPrev.y)
  }

  const nextZoom = clampZoom(viewport.zoom * (distNext / distPrev))
  const worldMidX = (midPrev.x - viewport.x) / viewport.zoom
  const worldMidY = (midPrev.y - viewport.y) / viewport.zoom
  return {
    x: midNext.x - worldMidX * nextZoom,
    y: midNext.y - worldMidY * nextZoom,
    zoom: nextZoom,
  }
}

export interface MinimapGeometry {
  scale: number
  offsetX: number
  offsetY: number
  viewRect: { x: number; y: number; width: number; height: number }
  toWorld: (pt: Point) => Point
}

export function computeMinimapGeometry(
  bounds: Size,
  viewport: Viewport,
  viewportSize: Size,
  minimapSize: Size,
  pad = 6
): MinimapGeometry {
  const innerW = Math.max(1, minimapSize.width - pad * 2)
  const innerH = Math.max(1, minimapSize.height - pad * 2)
  const scale = Math.min(innerW / Math.max(1, bounds.width), innerH / Math.max(1, bounds.height))
  const offsetX = (minimapSize.width - bounds.width * scale) / 2
  const offsetY = (minimapSize.height - bounds.height * scale) / 2

  const visLeft = -viewport.x / viewport.zoom
  const visTop = -viewport.y / viewport.zoom
  const visW = viewportSize.width / viewport.zoom
  const visH = viewportSize.height / viewport.zoom

  return {
    scale,
    offsetX,
    offsetY,
    viewRect: {
      x: offsetX + visLeft * scale,
      y: offsetY + visTop * scale,
      width: visW * scale,
      height: visH * scale,
    },
    toWorld: (pt: Point) => ({
      x: (pt.x - offsetX) / scale,
      y: (pt.y - offsetY) / scale,
    }),
  }
}

export interface ViewportScheduler {
  schedule: (viewport: Viewport) => void
  commitDirect: (viewport: Viewport) => void
  cancel: () => void
  hasPending: () => boolean
}

/**
 * Coalesces per-frame drag/pinch viewport updates into a single rAF while
 * guaranteeing that any direct viewport write or gesture cancel (`pointercancel`)
 * immediately cancels the queued frame so a stale rAF can never overwrite state.
 */
export function createViewportScheduler(
  onCommit: (viewport: Viewport) => void,
  scheduleFrame: (cb: FrameRequestCallback) => number = (cb) => window.requestAnimationFrame(cb),
  cancelFrame: (id: number) => void = (id) => window.cancelAnimationFrame(id)
): ViewportScheduler {
  let rafId: number | null = null
  let pending: Viewport | null = null

  const cancel = () => {
    if (rafId !== null) {
      cancelFrame(rafId)
      rafId = null
    }
    pending = null
  }

  const schedule = (viewport: Viewport) => {
    pending = viewport
    if (rafId !== null) return
    rafId = scheduleFrame(() => {
      rafId = null
      const next = pending
      pending = null
      if (next !== null) {
        onCommit(next)
      }
    })
  }

  const commitDirect = (viewport: Viewport) => {
    cancel()
    onCommit(viewport)
  }

  return {
    schedule,
    commitDirect,
    cancel,
    hasPending: () => rafId !== null,
  }
}
