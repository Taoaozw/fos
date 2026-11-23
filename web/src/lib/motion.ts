import { useEffect, type DependencyList, type RefObject } from "react"
import gsap from "gsap"

export const EASE_OUT = "power3.out"
export const EASE_SNAP = "power2.out"

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return true
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches
}

/**
 * Staggered reveal for matching child elements inside a container.
 * Cleans up safely under React StrictMode via gsap.context().
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

    const limit = options?.maxItems ?? 36
    const animated = targets.slice(0, limit)

    const ctx = gsap.context(() => {
      gsap.fromTo(
        animated,
        {
          opacity: 0,
          y: options?.y ?? 8,
          x: options?.x ?? 0,
        },
        {
          opacity: 1,
          y: 0,
          x: 0,
          duration: options?.duration ?? 0.28,
          stagger: Math.min(options?.stagger ?? 0.025, 0.25 / Math.max(1, animated.length)),
          ease: EASE_OUT,
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
  options?: { x?: number; y?: number; duration?: number }
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
          y: options?.y ?? 6,
        },
        {
          opacity: 1,
          x: 0,
          y: 0,
          duration: options?.duration ?? 0.24,
          ease: EASE_OUT,
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
        { opacity: 0, scale: 0.35 },
        {
          opacity: 1,
          scale: 1,
          duration: 0.26,
          stagger: { amount: 0.28, from: "start" },
          ease: EASE_SNAP,
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
      duration: 0.48,
      ease: EASE_OUT,
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
 * Imperative spin animation for refresh icons.
 */
export function spinOnce(el: HTMLElement | null) {
  if (!el || prefersReducedMotion()) return
  gsap.fromTo(
    el,
    { rotation: 0 },
    {
      rotation: 360,
      duration: 0.45,
      ease: EASE_OUT,
      clearProps: "transform",
    }
  )
}
