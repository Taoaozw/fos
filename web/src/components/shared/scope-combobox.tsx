import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react"
import { IconCheck, IconPlus, IconSearch, IconSelector } from "@tabler/icons-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"

interface ScopeComboboxProps {
  value: string
  onChange: (value: string) => void
  scopes: string[]
  placeholder?: string
  className?: string
  ariaLabel?: string
}

function normalizeCandidate(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/\\/g, "/")
    .replace(/^\/+|\/+$/g, "")
    .replace(/\/+/g, "/")
}

export function ScopeCombobox({
  value,
  onChange,
  scopes,
  placeholder = "选择或新增 Scope...",
  className,
  ariaLabel = "选择或新增作用域 Scope",
}: ScopeComboboxProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const allScopes = useMemo(() => {
    const set = new Set<string>(["global"])
    for (const s of scopes) {
      const clean = s.trim()
      if (clean) set.add(clean)
    }
    const cur = value.trim()
    if (cur) set.add(cur)
    return [...set].sort((a, b) => (a === "global" ? -1 : b === "global" ? 1 : a.localeCompare(b)))
  }, [scopes, value])

  const normalizedQuery = useMemo(() => normalizeCandidate(query), [query])

  const filteredScopes = useMemo(() => {
    if (!normalizedQuery) return allScopes
    return allScopes.filter((s) => s.toLowerCase().includes(normalizedQuery))
  }, [allScopes, normalizedQuery])

  const canCreate = Boolean(
    normalizedQuery && !allScopes.some((s) => s.toLowerCase() === normalizedQuery)
  )

  const totalItems = filteredScopes.length + (canCreate ? 1 : 0)

  useEffect(() => {
    if (!open) {
      setQuery("")
      setActiveIndex(0)
      return
    }
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0)
    return () => window.clearTimeout(timer)
  }, [open])

  useEffect(() => {
    setActiveIndex(0)
  }, [normalizedQuery])

  const selectScope = (next: string) => {
    onChange(next)
    setOpen(false)
    setQuery("")
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      if (totalItems > 0) setActiveIndex((i) => (i + 1) % totalItems)
      return
    }
    if (e.key === "ArrowUp") {
      e.preventDefault()
      if (totalItems > 0) setActiveIndex((i) => (i - 1 + totalItems) % totalItems)
      return
    }
    if (e.key === "Enter") {
      e.preventDefault()
      e.stopPropagation()
      if (activeIndex < filteredScopes.length) {
        selectScope(filteredScopes[activeIndex])
      } else if (canCreate) {
        selectScope(normalizedQuery)
      }
      return
    }
    if (e.key === "Escape") {
      e.stopPropagation()
      setOpen(false)
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            type="button"
            variant="outline"
            size="sm"
            role="combobox"
            aria-expanded={open}
            aria-label={ariaLabel}
            className={cn(
              "h-8 w-full justify-between px-2.5 font-mono text-xs font-normal",
              !value && "text-muted-foreground",
              className
            )}
          />
        }
      >
        <span className="truncate">{value || placeholder}</span>
        <IconSelector className="size-3.5 shrink-0 text-muted-foreground" />
      </PopoverTrigger>

      <PopoverContent
        align="start"
        sideOffset={4}
        className="w-(--anchor-width) min-w-56 gap-0 overflow-hidden p-0"
      >
        <div className="relative flex items-center border-b border-border/80 px-2 py-1.5">
          <IconSearch className="pointer-events-none absolute left-4 size-3.5 text-muted-foreground" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="搜索或输入新 Scope..."
            className="h-7 border-0 bg-transparent pl-7 font-mono text-xs shadow-none focus-visible:ring-0"
          />
        </div>

        <div className="flex max-h-56 flex-col gap-0.5 overflow-y-auto p-1" role="listbox">
          {filteredScopes.length === 0 && !canCreate ? (
            <div className="px-2 py-4 text-center text-xs text-muted-foreground">
              无匹配 Scope
            </div>
          ) : (
            filteredScopes.map((scope, idx) => {
              const selected = scope === value
              const highlighted = idx === activeIndex
              return (
                <Button
                  key={scope}
                  type="button"
                  variant="ghost"
                  size="sm"
                  role="option"
                  aria-selected={selected}
                  onMouseEnter={() => setActiveIndex(idx)}
                  onClick={() => selectScope(scope)}
                  className={cn(
                    "h-7 w-full justify-between px-2 font-mono text-xs font-normal",
                    highlighted && "bg-accent text-accent-foreground"
                  )}
                >
                  <span className="truncate">{scope}</span>
                  {selected && <IconCheck className="size-3.5 shrink-0 text-chart-1" />}
                </Button>
              )
            })
          )}

          {canCreate && (
            <>
              {filteredScopes.length > 0 && <Separator className="my-1" />}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                role="option"
                aria-selected={false}
                onMouseEnter={() => setActiveIndex(filteredScopes.length)}
                onClick={() => selectScope(normalizedQuery)}
                className={cn(
                  "h-7 w-full justify-start gap-1.5 px-2 text-xs font-normal text-chart-1 hover:text-chart-1",
                  activeIndex === filteredScopes.length && "bg-accent"
                )}
              >
                <IconPlus className="size-3.5 shrink-0" />
                <span className="shrink-0">新增</span>
                <Badge variant="secondary" className="max-w-full truncate rounded-sm px-1.5 font-mono text-[11px]">
                  {normalizedQuery}
                </Badge>
              </Button>
            </>
          )}
        </div>

        <div className="border-t border-border/70 bg-muted/30 px-2.5 py-1.5 text-[11px] text-muted-foreground">
          输入新路径回车即可创建（如 <span className="font-mono">eng/backend/rust</span>）
        </div>
      </PopoverContent>
    </Popover>
  )
}
