export const SCOPE_ALL = "__all__"
export const SCOPE_REPOS = "__repos__"

/** Canonical domain roots matching `fos/scopes.py` DOMAIN_ROOTS. */
const DOMAIN_ROOTS = new Set([
  "eng",
  "tech",
  "product",
  "design",
  "writing",
  "biz",
  "finance",
  "life",
  "research",
  "management",
])

export function isRepoScope(scope: string): boolean {
  const s = scope.trim()
  if (!s || s === "global") return false
  if (s.startsWith("repo/")) return true
  if (s.includes("/")) return false
  return !DOMAIN_ROOTS.has(s)
}

export function matchesScopeFilter(nodeScope: string, filterScope: string): boolean {
  if (!filterScope || filterScope === SCOPE_ALL) return true
  if (filterScope === SCOPE_REPOS) return isRepoScope(nodeScope)
  return nodeScope === filterScope || nodeScope.startsWith(`${filterScope}/`)
}

export interface ScopeTreeNode {
  key: string
  label: string
  directCount: number
  totalCount: number
  children: ScopeTreeNode[]
  isVirtual?: boolean
}

export interface ScopeBreadcrumb {
  key: string
  label: string
}

export interface ScopeTabItem {
  key: string
  label: string
  count: number
  hasChildren: boolean
}

export interface ScopeBarView {
  breadcrumbs: ScopeBreadcrumb[]
  tabs: ScopeTabItem[]
  activeKey: string
  levelKey: string
}

interface MutableNode {
  key: string
  label: string
  directCount: number
  totalCount: number
  children: Map<string, MutableNode>
  isVirtual?: boolean
}

function getOrCreate(
  parent: MutableNode,
  key: string,
  label: string,
  isVirtual = false
): MutableNode {
  let child = parent.children.get(key)
  if (!child) {
    child = {
      key,
      label,
      directCount: 0,
      totalCount: 0,
      children: new Map(),
      isVirtual,
    }
    parent.children.set(key, child)
  }
  return child
}

function sortOrder(a: ScopeTreeNode, b: ScopeTreeNode): number {
  const rank = (n: ScopeTreeNode) => {
    if (n.key === "global") return 0
    if (n.key === SCOPE_REPOS) return 2
    return 1
  }
  const ra = rank(a)
  const rb = rank(b)
  if (ra !== rb) return ra - rb
  if (b.totalCount !== a.totalCount) return b.totalCount - a.totalCount
  return a.label.localeCompare(b.label)
}

function finalize(node: MutableNode): ScopeTreeNode {
  const children = [...node.children.values()].map(finalize).sort(sortOrder)
  const childrenTotal = children.reduce((acc, c) => acc + c.totalCount, 0)
  return {
    key: node.key,
    label: node.label,
    directCount: node.directCount,
    totalCount: node.directCount + childrenTotal,
    children,
    isVirtual: node.isVirtual,
  }
}

/**
 * Build a clean hierarchical tree from all known scopes + live filtered node counts.
 * Standalone repository scopes (e.g. `fos`, `goto`, `repo/foo`) are grouped under
 * virtual branch `__repos__` ("项目") so root tabs remain compact and single-row.
 */
export function buildScopeTree(
  allScopes: string[],
  nodeScopes: string[]
): ScopeTreeNode {
  const root: MutableNode = {
    key: SCOPE_ALL,
    label: "全部",
    directCount: 0,
    totalCount: 0,
    children: new Map(),
    isVirtual: true,
  }

  const countMap = new Map<string, number>()
  for (const s of nodeScopes) {
    const clean = s.trim()
    if (!clean) continue
    countMap.set(clean, (countMap.get(clean) ?? 0) + 1)
  }

  const scopeSet = new Set<string>()
  for (const s of allScopes) {
    const clean = s.trim()
    if (clean) scopeSet.add(clean)
  }
  for (const s of countMap.keys()) {
    scopeSet.add(s)
  }

  for (const scope of scopeSet) {
    const count = countMap.get(scope) ?? 0
    if (scope === "global") {
      const g = getOrCreate(root, "global", "global")
      g.directCount += count
      continue
    }

    if (isRepoScope(scope)) {
      const repos = getOrCreate(root, SCOPE_REPOS, "项目", true)
      const label = scope.startsWith("repo/") ? scope.slice(5) : scope
      const leaf = getOrCreate(repos, scope, label)
      leaf.directCount += count
      continue
    }

    const parts = scope.split("/").filter(Boolean)
    let cursor = root
    let prefix = ""
    parts.forEach((seg, idx) => {
      prefix = prefix ? `${prefix}/${seg}` : seg
      cursor = getOrCreate(cursor, prefix, seg)
      if (idx === parts.length - 1) {
        cursor.directCount += count
      }
    })
  }

  return finalize(root)
}

function findNodePath(
  root: ScopeTreeNode,
  targetKey: string
): ScopeTreeNode[] | null {
  if (root.key === targetKey) return [root]
  for (const child of root.children) {
    const sub = findNodePath(child, targetKey)
    if (sub) return [root, ...sub]
  }
  return null
}

/**
 * Derive single-row progressive breadcrumb + active-level tabs purely from `(tree, selectedScope)`.
 * - Selecting a branch with children drills into that branch (showing its children as tabs).
 * - Selecting a leaf stays at its parent's level so siblings stay 1-click reachable.
 */
export function resolveScopeBar(
  tree: ScopeTreeNode,
  selectedScope: string
): ScopeBarView {
  const path = findNodePath(tree, selectedScope) ?? [tree]
  const current = path[path.length - 1]

  const isRoot = path.length === 1
  const drillsIntoCurrent = !isRoot && current.children.length > 0

  // Parent container whose children become the visible tabs
  const containerPath = drillsIntoCurrent ? path : path.slice(0, Math.max(1, path.length - 1))
  const container = containerPath[containerPath.length - 1]

  const breadcrumbs: ScopeBreadcrumb[] =
    containerPath.length > 1
      ? containerPath.map((n) => ({ key: n.key, label: n.label }))
      : []

  const allTabLabel = container.key === SCOPE_ALL ? "全部" : `全部 ${container.label}`
  const tabs: ScopeTabItem[] = [
    {
      key: container.key,
      label: allTabLabel,
      count: container.totalCount,
      hasChildren: false,
    },
    ...container.children.map((child) => ({
      key: child.key,
      label: child.label,
      count: child.totalCount,
      hasChildren: child.children.length > 0,
    })),
  ]

  const activeKey = tabs.some((t) => t.key === current.key) ? current.key : container.key

  return {
    breadcrumbs,
    tabs,
    activeKey,
    levelKey: container.key,
  }
}
