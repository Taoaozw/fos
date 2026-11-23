import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

export type StatusTone = "neutral" | "success" | "info" | "warning" | "purple" | "danger"

const TONE_STYLES: Record<StatusTone, { pill: string; dot: string }> = {
  neutral: {
    pill: "bg-muted text-muted-foreground",
    dot: "bg-muted-foreground/65",
  },
  success: {
    pill: "bg-chart-2/12 text-chart-2",
    dot: "bg-chart-2",
  },
  info: {
    pill: "bg-chart-1/12 text-chart-1",
    dot: "bg-chart-1",
  },
  warning: {
    pill: "bg-chart-3/15 text-chart-3",
    dot: "bg-chart-3",
  },
  purple: {
    pill: "bg-chart-4/12 text-chart-4",
    dot: "bg-chart-4",
  },
  danger: {
    pill: "bg-destructive/12 text-destructive",
    dot: "bg-destructive",
  },
}

interface StatusPillProps {
  tone?: StatusTone
  dot?: boolean
  pulse?: boolean
  mono?: boolean
  className?: string
  children: ReactNode
}

export function StatusPill({
  tone = "neutral",
  dot = true,
  pulse = false,
  mono = false,
  className,
  children,
}: StatusPillProps) {
  const styles = TONE_STYLES[tone]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium leading-5 whitespace-nowrap",
        mono && "font-mono text-[11px]",
        styles.pill,
        className
      )}
    >
      {dot && (
        <span className="relative inline-flex size-1.5 shrink-0">
          {pulse && (
            <span
              className={cn(
                "absolute inset-0 animate-ping rounded-full opacity-55",
                styles.dot
              )}
            />
          )}
          <span className={cn("relative inline-flex size-1.5 rounded-full", styles.dot)} />
        </span>
      )}
      {children}
    </span>
  )
}
