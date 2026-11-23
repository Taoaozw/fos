import { useEffect, useLayoutEffect, useState, type DependencyList, type RefObject } from "react"
import gsap from "gsap"

export const EASE_SPRING = "expo.out"
export const EASE_OUT = "power3.out"
export const EASE_SNAP = "power2.out"

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return true
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches
}

/**
 * Staggered reveal for matching child elements inside a container.
 * Uses fast expo-out deceleration so lists feel crisp and responsive.
 */
export function useRevealGroup(
  containerRef: RefObject<HTMLElement | null>,
  selector: string,
  deps: DependencyList = [],
  options?: {
    y?: number
    x?: number
    duration?: number
    stagger?: number
    maxItems?: number
  }
) {
  useEffect(() => {
    const el = containerRef.current
    if (!el || prefersReducedMotion()) return

    const targets = Array.from(el.querySelectorAll<HTMLElement>(selector))
    if (targets.length === 0) return

    const limit = options?.maxItems ?? 32
    const animated = targets.slice(0, limit)

    const ctx = gsap.context(() => {
      gsap.fromTo(
        animated,
        {
          opacity: 0,
          y: options?.y ?? 7,
          x: options?.x ?? 0,
        },
        {
          opacity: 1,
          y: 0,
          x: 0,
          duration: options?.duration ?? 0.34,
          stagger: Math.min(options?.stagger ?? 0.024, 0.2 / Math.max(1, animated.length)),
          ease: EASE_SPRING,
          clearProps: "transform,opacity",
        }
      )
    }, el)

    return () => ctx.revert()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

/**
 * Smooth entrance for a single panel or detail view when its key/content changes.
 */
export function usePanelTransition(
  ref: RefObject<HTMLElement | null>,
  deps: DependencyList,
  options?: { x?: number; y?: number; duration?: number; scale?: number }
) {
  useEffect(() => {
    const el = ref.current
    if (!el || prefersReducedMotion()) return

    const ctx = gsap.context(() => {
      gsap.fromTo(
        el,
        {
          opacity: 0,
          x: options?.x ?? 0,
          y: options?.y ?? 5,
          scale: options?.scale ?? 0.996,
        },
        {
          opacity: 1,
          x: 0,
          y: 0,
          scale: 1,
          duration: options?.duration ?? 0.3,
          ease: EASE_SPRING,
          clearProps: "transform,opacity",
        }
      )
    }, el)

    return () => ctx.revert()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

/**
 * Ripple wave entrance for dot-matrix visualizations.
 */
export function useMatrixWave(
  containerRef: RefObject<HTMLElement | null>,
  selector: string,
  deps: DependencyList = []
) {
  useEffect(() => {
    const el = containerRef.current
    if (!el || prefersReducedMotion()) return

    const dots = el.querySelectorAll<HTMLElement>(selector)
    if (dots.length === 0) return

    const ctx = gsap.context(() => {
      gsap.fromTo(
        dots,
        { opacity: 0, scale: 0.4 },
        {
          opacity: 1,
          scale: 1,
          duration: 0.28,
          stagger: { amount: 0.26, from: "start" },
          ease: EASE_SPRING,
          clearProps: "transform,opacity",
        }
      )
    }, el)

    return () => ctx.revert()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

/**
 * Wave entrance for segmented vertical tick bars (Context-bar style).
 */
export function useTickWave(
  containerRef: RefObject<HTMLElement | null>,
  selector: string,
  deps: DependencyList = []
) {
  useEffect(() => {
    const el = containerRef.current
    if (!el || prefersReducedMotion()) return

    const ticks = el.querySelectorAll<HTMLElement>(selector)
    if (ticks.length === 0) return

    const ctx = gsap.context(() => {
      gsap.fromTo(
        ticks,
        { scaleY: 0.35, opacity: 0.35 },
        {
          scaleY: 1,
          opacity: 1,
          duration: 0.36,
          stagger: { amount: 0.22, from: "start" },
          ease: EASE_SPRING,
          clearProps: "transform,opacity",
        }
      )
    }, el)

    return () => ctx.revert()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

/**
 * Subtle numeric count-up for KPI readouts (integers or percentages like "86.7%").
 */
export function useCountUp(
  ref: RefObject<HTMLElement | null>,
  rawValue: number | string
) {
  useEffect(() => {
    const el = ref.current
    if (!el || prefersReducedMotion()) return

    const str = String(rawValue)
    const match = str.match(/^(-?\d+(?:\.\d+)?)(.*)$/)
    if (!match) return

    const target = Number(match[1])
    if (!Number.isFinite(target)) return
    const decimals = match[1].includes(".") ? match[1].split(".")[1].length : 0
    const suffix = match[2] ?? ""

    const state = { val: 0 }
    const tween = gsap.to(state, {
      val: target,
      duration: 0.52,
      ease: EASE_SPRING,
      onUpdate: () => {
        el.textContent = `${state.val.toFixed(decimals)}${suffix}`
      },
      onComplete: () => {
        el.textContent = str
      },
    })

    return () => {
      tween.kill()
      el.textContent = str
    }
  }, [ref, rawValue])
}

/**
 * Measures the active tab inside a TabsList container so a gliding indicator bar
 * transitions smoothly between tabs.
 */
export function useSlidingIndicator(
  listRef: RefObject<HTMLElement | null>,
  activeKey: string
): { left: number; width: number; ready: boolean } {
  const [rect, setRect] = useState<{ left: number; width: number; ready: boolean }>({
    left: 0,
    width: 0,
    ready: false,
  })

  useLayoutEffect(() => {
    const container = listRef.current
    if (!container) return

    const measure = () => {
      const activeEl = container.querySelector<HTMLElement>(
        '[role="tab"][data-active], [role="tab"][aria-selected="true"]'
      )
      if (!activeEl) return
      const nextLeft = activeEl.offsetLeft
      const nextWidth = activeEl.offsetWidth
      setRect((prev) =>
        prev.left === nextLeft && prev.width === nextWidth && prev.ready
          ? prev
          : { left: nextLeft, width: nextWidth, ready: true }
      )
    }

    measure()
    const raf = window.requestAnimationFrame(measure)

    let ro: ResizeObserver | null = null
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(measure)
      ro.observe(container)
    }

    return () => {
      window.cancelAnimationFrame(raf)
      ro?.disconnect()
    }
  }, [listRef, activeKey])

  return rect
}

/**
 * Imperative spin animation for refresh icons.
 */
export function spinOnce(el: HTMLElement | null) {
  if (!el || prefersReducedMotion()) return
  gsap.fromTo(
    el,
    { rotation: 0 },
    {
      rotation: 360,
      duration: 0.5,
      ease: EASE_SPRING,
      clearProps: "transform",
    }
  )
}
