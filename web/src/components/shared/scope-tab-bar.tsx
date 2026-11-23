import { useLayoutEffect, useMemo, useRef, useState } from "react"
import { IconChevronLeft, IconChevronRight } from "@tabler/icons-react"
import { Button } from "@/components/ui/button"
import { usePanelTransition } from "@/lib/motion"
import {
  SCOPE_ALL,
  SCOPE_REPOS,
  buildScopeTree,
  resolveScopeBar,
} from "@/lib/scope-tree"
import { cn } from "@/lib/utils"

interface ScopeTabBarProps {
  value: string
  onChange: (scope: string) => void
  allScopes: string[]
  nodeScopes: string[]
  className?: string
}

export function ScopeTabBar({
  value,
  onChange,
  allScopes,
  nodeScopes,
  className,
}: ScopeTabBarProps) {
  const tree = useMemo(
    () => buildScopeTree(allScopes, nodeScopes),
    [allScopes, nodeScopes]
  )
  const view = useMemo(() => resolveScopeBar(tree, value), [tree, value])

  const trackRef = useRef<HTMLDivElement | null>(null)
  const prevLevelRef = useRef<string>(view.levelKey)
  const [pill, setPill] = useState<{
    left: number
    width: number
    ready: boolean
    animate: boolean
  }>({ left: 0, width: 0, ready: false, animate: false })

  usePanelTransition(trackRef, [view.levelKey], { x: 6, y: 0, scale: 1, duration: 0.22 })

  useLayoutEffect(() => {
    const track = trackRef.current
    if (!track) return
    const levelChanged = prevLevelRef.current !== view.levelKey
    prevLevelRef.current = view.levelKey

    const measure = (allowAnimate: boolean) => {
      const activeEl = track.querySelector<HTMLElement>('[data-scope-tab-active="true"]')
      if (!activeEl) return
      const nextLeft = activeEl.offsetLeft
      const nextWidth = activeEl.offsetWidth
      setPill((prev) =>
        prev.left === nextLeft &&
        prev.width === nextWidth &&
        prev.ready &&
        prev.animate === allowAnimate
          ? prev
          : { left: nextLeft, width: nextWidth, ready: true, animate: allowAnimate }
      )
    }

    measure(!levelChanged && pill.ready)

    let ro: ResizeObserver | null = null
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => measure(false))
      ro.observe(track)
    }
    return () => ro?.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.levelKey, view.activeKey, view.tabs.length])

  const isNaturalLabel = (key: string) => key === SCOPE_ALL || key === SCOPE_REPOS

  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
        className
      )}
    >
      {/* Progressive Breadcrumb Trail when drilled into a branch */}
      {view.breadcrumbs.length > 0 && (
        <>
          <div className="flex shrink-0 items-center gap-0.5">
            {view.breadcrumbs.map((crumb, idx) => {
              const isLast = idx === view.breadcrumbs.length - 1
              return (
                <div key={crumb.key} className="flex items-center gap-0.5">
                  {idx > 0 && (
                    <IconChevronRight className="size-3 shrink-0 text-muted-foreground/45" />
                  )}
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    onClick={() => onChange(crumb.key)}
                    className={cn(
                      "h-7 rounded-lg px-1.5 text-xs font-normal",
                      !isNaturalLabel(crumb.key) && "font-mono",
                      isLast
                        ? "font-medium text-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {idx === 0 && <IconChevronLeft className="size-3.5 text-muted-foreground" />}
                    {crumb.label}
                  </Button>
                </div>
              )
            })}
          </div>
          <span aria-hidden="true" className="mx-0.5 h-4 w-px shrink-0 bg-border/80" />
        </>
      )}

      {/* Single-Row Segmented Scope Tabs with Gliding Elevated Pill */}
      <div
        ref={trackRef}
        role="tablist"
        aria-label="Scope 作用域分层切换"
        className="relative inline-flex shrink-0 items-center gap-0.5 rounded-xl bg-muted/60 p-0.5"
      >
        {pill.ready && (
          <span
            aria-hidden="true"
            style={{
              transform: `translate3d(${pill.left}px, 0, 0)`,
              width: `${pill.width}px`,
            }}
            className={cn(
              "pointer-events-none absolute top-0.5 bottom-0.5 left-0 rounded-[9px] bg-card shadow-2xs ring-1 ring-border/75",
              pill.animate &&
                "transition-[transform,width] duration-250 ease-[cubic-bezier(0.22,1,0.36,1)]"
            )}
          />
        )}

        {view.tabs.map((tab) => {
          const active = tab.key === view.activeKey
          const isContainerAll = tab.key === view.levelKey
          const mono = !isNaturalLabel(tab.key) && !isContainerAll
          return (
            <Button
              key={tab.key}
              type="button"
              variant="ghost"
              size="xs"
              role="tab"
              aria-selected={active}
              data-scope-tab-active={active ? "true" : undefined}
              onClick={() => onChange(tab.key)}
              className={cn(
                "group relative z-10 h-7 gap-1 rounded-[9px] px-2.5 text-xs transition-colors hover:bg-transparent",
                mono && "font-mono",
                active
                  ? "font-medium text-foreground"
                  : "font-normal text-muted-foreground hover:text-foreground",
                tab.count === 0 && !active && "opacity-55"
              )}
            >
              <span>{tab.label}</span>
              <span
                className={cn(
                  "font-mono text-[11px]",
                  active ? "text-muted-foreground" : "text-muted-foreground/70"
                )}
              >
                {tab.count}
              </span>
              {tab.hasChildren && (
                <IconChevronRight className="size-3 -mr-0.5 text-muted-foreground/55 transition-transform duration-150 group-hover:translate-x-0.5" />
              )}
            </Button>
          )
        })}
      </div>
    </div>
  )
}
