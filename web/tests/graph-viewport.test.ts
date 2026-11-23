import { describe, expect, test } from "bun:test"
import {
  MAX_ZOOM,
  MIN_ZOOM,
  clampZoom,
  computeMinimapGeometry,
  computePinchViewport,
  computeWheelAction,
  createViewportScheduler,
  fitBoundsToViewport,
  focusWorldPoint,
  panBy,
  zoomAtPoint,
  type Viewport,
} from "../src/lib/graph-viewport"

describe("graph-viewport primitives", () => {
  test("clampZoom enforces bounds and guards non-finite input", () => {
    expect(clampZoom(0.01)).toBe(MIN_ZOOM)
    expect(clampZoom(10)).toBe(MAX_ZOOM)
    expect(clampZoom(1.25)).toBe(1.25)
    expect(clampZoom(Number.NaN)).toBe(1)
  })

  test("zoomAtPoint keeps cursor world coordinate fixed", () => {
    const start: Viewport = { x: 40, y: -60, zoom: 0.8 }
    const anchor = { x: 420, y: 260 }
    const worldBefore = {
      x: (anchor.x - start.x) / start.zoom,
      y: (anchor.y - start.y) / start.zoom,
    }

    const zoomed = zoomAtPoint(start, anchor, 1.6)
    const worldAfter = {
      x: (anchor.x - zoomed.x) / zoomed.zoom,
      y: (anchor.y - zoomed.y) / zoomed.zoom,
    }

    expect(zoomed.zoom).toBeCloseTo(1.6, 6)
    expect(worldAfter.x).toBeCloseTo(worldBefore.x, 6)
    expect(worldAfter.y).toBeCloseTo(worldBefore.y, 6)
  })

  test("fitBoundsToViewport scales down large graphs past old 0.42 floor and centers", () => {
    const vp = fitBoundsToViewport({ width: 3800, height: 2400 }, { width: 1200, height: 600 }, 28)
    expect(vp.zoom).toBeLessThan(0.35)
    expect(vp.zoom).toBeGreaterThanOrEqual(MIN_ZOOM)
    expect(vp.x).toBeGreaterThan(0)
    expect(vp.y).toBeCloseTo(28, 4)
  })

  test("focusWorldPoint centers target node in viewport", () => {
    const vp = focusWorldPoint({ x: 900, y: 450 }, { width: 1000, height: 600 }, 1.2)
    expect(vp.x + 900 * vp.zoom).toBeCloseTo(500, 6)
    expect(vp.y + 450 * vp.zoom).toBeCloseTo(300, 6)
  })

  test("computeWheelAction supports direct wheel zoom, trackpad pinch, and shift-pan", () => {
    const zoomIn = computeWheelAction({
      deltaX: 0,
      deltaY: -100,
      deltaMode: 0,
      ctrlKey: false,
      metaKey: false,
      shiftKey: false,
    })
    expect(zoomIn.type).toBe("zoom")
    if (zoomIn.type === "zoom") expect(zoomIn.factor).toBeGreaterThan(1.1)

    const pinchOut = computeWheelAction({
      deltaX: 0,
      deltaY: 6,
      deltaMode: 0,
      ctrlKey: true,
      metaKey: false,
      shiftKey: false,
    })
    expect(pinchOut.type).toBe("zoom")
    if (pinchOut.type === "zoom") {
      expect(pinchOut.factor).toBeLessThan(1)
      expect(pinchOut.factor).toBeGreaterThan(0.9)
    }

    const shiftPan = computeWheelAction({
      deltaX: 0,
      deltaY: 80,
      deltaMode: 0,
      ctrlKey: false,
      metaKey: false,
      shiftKey: true,
    })
    expect(shiftPan).toEqual({ type: "pan", dx: -80, dy: 0 })

    const horizWheel = computeWheelAction({
      deltaX: 45,
      deltaY: 0,
      deltaMode: 0,
      ctrlKey: false,
      metaKey: false,
      shiftKey: false,
    })
    expect(horizWheel).toEqual({ type: "pan", dx: -45, dy: 0 })
  })

  test("computePinchViewport zooms around midpoint and tracks pan simultaneously", () => {
    const start: Viewport = { x: 100, y: 50, zoom: 1.0 }
    const p1Prev = { x: 200, y: 200 }
    const p2Prev = { x: 300, y: 200 }
    // Double finger distance while shifting midpoint right by 40px
    const p1Next = { x: 190, y: 200 }
    const p2Next = { x: 390, y: 200 }

    const next = computePinchViewport(start, p1Prev, p2Prev, p1Next, p2Next)
    expect(next.zoom).toBeCloseTo(2.0, 6)
    const worldMidBefore = (250 - start.x) / start.zoom
    const worldMidAfter = (290 - next.x) / next.zoom
    expect(worldMidAfter).toBeCloseTo(worldMidBefore, 6)
  })

  test("computeMinimapGeometry maps viewport rect and inverts points accurately", () => {
    const vp: Viewport = { x: -200, y: -100, zoom: 0.5 }
    const geo = computeMinimapGeometry(
      { width: 2000, height: 1000 },
      vp,
      { width: 800, height: 400 },
      { width: 200, height: 100 },
      0
    )
    expect(geo.scale).toBeCloseTo(0.1, 6)
    expect(geo.viewRect.x).toBeCloseTo(40, 6)
    expect(geo.viewRect.y).toBeCloseTo(20, 6)
    expect(geo.viewRect.width).toBeCloseTo(160, 6)
    expect(geo.viewRect.height).toBeCloseTo(80, 6)

    const worldCenter = geo.toWorld({ x: 120, y: 60 })
    expect(worldCenter.x).toBeCloseTo(1200, 6)
    expect(worldCenter.y).toBeCloseTo(600, 6)
  })

  test("scheduler cancels queued rAF on commitDirect and cancel (LSN-059)", () => {
    let nextId = 1
    const queue = new Map<number, FrameRequestCallback>()
    const committed: Viewport[] = []

    const scheduler = createViewportScheduler(
      (vp) => committed.push(vp),
      (cb) => {
        const id = nextId++
        queue.set(id, cb)
        return id
      },
      (id) => {
        queue.delete(id)
      }
    )

    // 1. Direct update must cancel queued frame so stale drag frame never overwrites it
    scheduler.schedule({ x: 10, y: 20, zoom: 0.9 })
    expect(scheduler.hasPending()).toBe(true)
    expect(queue.size).toBe(1)

    scheduler.commitDirect({ x: 0, y: 0, zoom: 0.4 })
    expect(scheduler.hasPending()).toBe(false)
    expect(queue.size).toBe(0)
    expect(committed).toEqual([{ x: 0, y: 0, zoom: 0.4 }])

    // 2. pointercancel path: cancel drops queued frame completely
    scheduler.schedule(panBy({ x: 0, y: 0, zoom: 0.4 }, 50, 30))
    expect(queue.size).toBe(1)
    scheduler.cancel()
    expect(scheduler.hasPending()).toBe(false)
    expect(queue.size).toBe(0)
    expect(committed.length).toBe(1)

    // 3. Coalescing: multiple schedules in same frame commit only latest viewport
    scheduler.schedule({ x: 1, y: 1, zoom: 1 })
    scheduler.schedule({ x: 2, y: 3, zoom: 1 })
    expect(queue.size).toBe(1)
    const [[, cb]] = [...queue.entries()]
    queue.clear()
    cb(16)
    expect(committed[committed.length - 1]).toEqual({ x: 2, y: 3, zoom: 1 })
  })
})
