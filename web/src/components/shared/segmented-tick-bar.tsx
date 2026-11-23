import { useMemo, useRef, useState } from "react"
import { IconPinFilled } from "@tabler/icons-react"
import { Button } from "@/components/ui/button"
import { useTickWave } from "@/lib/motion"
import { cn } from "@/lib/utils"

export interface TickSegment {
  key: string
  label: string
  value: number
  displayValue?: string
  colorClass: string
  pinned?: boolean
  onClick?: () => void
}

interface SegmentedTickBarProps {
  title: string
  subtitle?: string
  summaryPrimary?: string
  summarySecondary?: string
  segments: TickSegment[]
  /** Optional denominator larger than sum(segments) so trailing ticks stay quiet/unfilled (e.g. L0 300t budget). */
  capacity?: number
  /** Optional threshold value (0..capacity) rendered as a crisp vertical marker line. */
  threshold?: number
  footnote?: string
  ticks?: number
  className?: string
}

export function SegmentedTickBar({
  title,
  subtitle,
  summaryPrimary,
  summarySecondary,
  segments,
  capacity,
  threshold,
  footnote,
  ticks = 56,
  className,
}: SegmentedTickBarProps) {
  const [hoveredKey, setHoveredKey] = useState<string | null>(null)
  const barRef = useRef<HTMLDivElement | null>(null)

  const nonZero = useMemo(() => segments.filter((s) => s.value > 0), [segments])
  const sum = useMemo(() => nonZero.reduce((acc, s) => acc + s.value, 0), [nonZero])
  const denom = Math.max(1, capacity ?? sum)

  const allocatedTicks = useMemo(() => {
    const out: Array<{ key: string | null; label: string; colorClass: string }> = []
    if (sum <= 0) {
      for (let i = 0; i < ticks; i++) {
        out.push({ key: null, label: "", colorClass: "bg-muted-foreground/15" })
      }
      return out
    }

    const filledTarget = capacity
      ? Math.min(ticks, Math.max(1, Math.round((sum / denom) * ticks)))
      : ticks

    let remaining = filledTarget
    const counts: Array<{ seg: TickSegment; count: number }> = []
    nonZero.forEach((seg, idx) => {
      if (idx === nonZero.length - 1) {
        counts.push({ seg, count: Math.max(remaining > 0 ? 1 : 0, remaining) })
        remaining = 0
      } else {
        const raw = Math.round((seg.value / sum) * filledTarget)
        const count = Math.min(remaining, Math.max(1, raw))
        counts.push({ seg, count })
        remaining = Math.max(0, remaining - count)
      }
    })

    for (const { seg, count } of counts) {
      for (let i = 0; i < count && out.length < ticks; i++) {
        out.push({ key: seg.key, label: seg.label, colorClass: seg.colorClass })
      }
    }
    while (out.length < ticks) {
      out.push({ key: null, label: "", colorClass: "bg-muted-foreground/15" })
    }
    return out
  }, [capacity, denom, nonZero, sum, ticks])

  const animSig = nonZero.map((s) => `${s.key}:${s.value}`).join("|")
  useTickWave(barRef, "[data-anim='tick']", [animSig, capacity])

  const thresholdPct =
    threshold !== undefined && denom > 0
      ? Math.min(98, Math.max(2, (threshold / denom) * 100))
      : null

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {/* Header: strong primary + quiet secondary */}
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-baseline gap-1.5">
          <span className="text-[14px] font-semibold tracking-tight text-foreground">{title}</span>
          {subtitle && (
            <span className="text-[13px] font-normal text-muted-foreground">{subtitle}</span>
          )}
        </div>
        {(summaryPrimary || summarySecondary) && (
          <div className="flex items-baseline gap-1 font-mono text-xs">
            {summaryPrimary && (
              <span className="font-semibold text-foreground">{summaryPrimary}</span>
            )}
            {summarySecondary && (
              <span className="text-muted-foreground">{summarySecondary}</span>
            )}
          </div>
        )}
      </div>

      {/* Dense rounded vertical tick bar */}
      <div ref={barRef} className="relative flex h-5 items-center gap-[2.5px]">
        {allocatedTicks.map((t, i) => {
          const isHovered = hoveredKey !== null && t.key === hoveredKey
          const isDimmed = hoveredKey !== null && t.key !== null && t.key !== hoveredKey
          return (
            <span
              key={i}
              data-anim="tick"
              onMouseEnter={() => t.key && setHoveredKey(t.key)}
              onMouseLeave={() => setHoveredKey(null)}
              title={t.label || undefined}
              className={cn(
                "h-4 flex-1 origin-center rounded-[2.5px] transition-[transform,opacity,filter] duration-200 ease-[cubic-bezier(0.22,1,0.36,1)]",
                t.colorClass,
                t.key ? "cursor-pointer" : "pointer-events-none",
                isHovered && "scale-y-[1.22] brightness-105",
                isDimmed && "opacity-25"
              )}
            />
          )
        })}

        {thresholdPct !== null && (
          <span
            aria-hidden="true"
            style={{ left: `${thresholdPct}%` }}
            className="pointer-events-none absolute top-[-3px] bottom-[-3px] w-[1.5px] -translate-x-1/2 rounded-full bg-foreground/35"
          />
        )}
      </div>

      {/* Soft interactive pill chips */}
      {nonZero.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {nonZero.map((seg) => {
            const active = hoveredKey === seg.key
            const dimmed = hoveredKey !== null && hoveredKey !== seg.key
            const chipContent = (
              <>
                <span className={cn("size-1.5 shrink-0 rounded-full", seg.colorClass)} />
                <span className="text-foreground">{seg.label}</span>
                <span className="font-mono text-[11px] text-muted-foreground">
                  {seg.displayValue ?? seg.value}
                </span>
                {seg.pinned && (
                  <IconPinFilled className="size-3 shrink-0 text-destructive/80" />
                )}
              </>
            )

            if (seg.onClick) {
              return (
                <Button
                  key={seg.key}
                  type="button"
                  variant="ghost"
                  size="xs"
                  onMouseEnter={() => setHoveredKey(seg.key)}
                  onMouseLeave={() => setHoveredKey(null)}
                  onClick={seg.onClick}
                  className={cn(
                    "h-7 gap-1.5 rounded-full border border-border/60 bg-muted/45 px-2.5 text-xs font-normal transition-all duration-200 hover:bg-muted/85",
                    active && "border-border bg-muted shadow-2xs",
                    dimmed && "opacity-55"
                  )}
                >
                  {chipContent}
                </Button>
              )
            }

            return (
              <span
                key={seg.key}
                onMouseEnter={() => setHoveredKey(seg.key)}
                onMouseLeave={() => setHoveredKey(null)}
                className={cn(
                  "inline-flex h-7 cursor-default items-center gap-1.5 rounded-full border border-border/60 bg-muted/45 px-2.5 text-xs transition-all duration-200",
                  active && "border-border bg-muted shadow-2xs",
                  dimmed && "opacity-55"
                )}
              >
                {chipContent}
              </span>
            )
          })}
        </div>
      )}

      {footnote && (
        <div className="text-xs text-muted-foreground">{footnote}</div>
      )}
    </div>
  )
}
