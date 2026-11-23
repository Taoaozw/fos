import { describe, expect, test } from "bun:test"
import {
  SCOPE_ALL,
  SCOPE_REPOS,
  buildScopeTree,
  isRepoScope,
  matchesScopeFilter,
  resolveScopeBar,
} from "../src/lib/scope-tree"

describe("scope-tree progressive breadcrumb tabs", () => {
  const scopes = [
    "global",
    "eng",
    "eng/ai",
    "eng/frontend",
    "eng/frontend/react",
    "eng/frontend/shadcn",
    "eng/backend",
    "eng/backend/jvm",
    "product",
    "fos",
    "goto",
  ]
  const nodeScopes = [
    "global",
    "global",
    "eng",
    "eng/ai",
    "eng/frontend",
    "eng/frontend/react",
    "eng/frontend/react",
    "eng/frontend/shadcn",
    "eng/backend/jvm",
    "product",
    "fos",
    "fos",
    "goto",
  ]

  test("classifies repo scopes vs global and domain scopes", () => {
    expect(isRepoScope("global")).toBe(false)
    expect(isRepoScope("eng")).toBe(false)
    expect(isRepoScope("eng/frontend/react")).toBe(false)
    expect(isRepoScope("product")).toBe(false)
    expect(isRepoScope("fos")).toBe(true)
    expect(isRepoScope("repo/my-app")).toBe(true)
  })

  test("matchesScopeFilter supports all, repos virtual group, and hierarchical prefixes", () => {
    expect(matchesScopeFilter("eng/frontend/react", SCOPE_ALL)).toBe(true)
    expect(matchesScopeFilter("fos", SCOPE_REPOS)).toBe(true)
    expect(matchesScopeFilter("eng/frontend", SCOPE_REPOS)).toBe(false)
    expect(matchesScopeFilter("eng/frontend/react", "eng")).toBe(true)
    expect(matchesScopeFilter("eng/frontend/react", "eng/frontend")).toBe(true)
    expect(matchesScopeFilter("eng/backend/jvm", "eng/frontend")).toBe(false)
  })

  test("root level groups repos under virtual branch and keeps global first", () => {
    const tree = buildScopeTree(scopes, nodeScopes)
    const view = resolveScopeBar(tree, SCOPE_ALL)

    expect(view.breadcrumbs).toEqual([])
    expect(view.activeKey).toBe(SCOPE_ALL)
    expect(view.tabs.map((t) => t.key)).toEqual([SCOPE_ALL, "global", "eng", "product", SCOPE_REPOS])
    expect(view.tabs.find((t) => t.key === SCOPE_ALL)?.count).toBe(13)
    expect(view.tabs.find((t) => t.key === "eng")?.count).toBe(7)
    expect(view.tabs.find((t) => t.key === "eng")?.hasChildren).toBe(true)
    expect(view.tabs.find((t) => t.key === SCOPE_REPOS)?.count).toBe(3)
    expect(view.tabs.find((t) => t.key === SCOPE_REPOS)?.hasChildren).toBe(true)
  })

  test("selecting root leaf stays at root level", () => {
    const tree = buildScopeTree(scopes, nodeScopes)
    const view = resolveScopeBar(tree, "global")
    expect(view.breadcrumbs).toEqual([])
    expect(view.activeKey).toBe("global")
    expect(view.levelKey).toBe(SCOPE_ALL)
  })

  test("selecting branch drills into children; selecting child leaf stays at branch level", () => {
    const tree = buildScopeTree(scopes, nodeScopes)

    // Drill into eng
    const engView = resolveScopeBar(tree, "eng")
    expect(engView.breadcrumbs.map((b) => b.key)).toEqual([SCOPE_ALL, "eng"])
    expect(engView.activeKey).toBe("eng")
    expect(engView.tabs.map((t) => t.key)).toEqual(["eng", "eng/frontend", "eng/ai", "eng/backend"])

    // Leaf under eng stays at eng level so siblings remain 1-click reachable
    const aiView = resolveScopeBar(tree, "eng/ai")
    expect(aiView.breadcrumbs.map((b) => b.key)).toEqual([SCOPE_ALL, "eng"])
    expect(aiView.activeKey).toBe("eng/ai")
    expect(aiView.levelKey).toBe("eng")

    // Drill deeper into eng/frontend
    const feView = resolveScopeBar(tree, "eng/frontend")
    expect(feView.breadcrumbs.map((b) => b.key)).toEqual([SCOPE_ALL, "eng", "eng/frontend"])
    expect(feView.tabs.map((t) => t.key)).toEqual([
      "eng/frontend",
      "eng/frontend/react",
      "eng/frontend/shadcn",
    ])

    // Selecting leaf eng/frontend/react stays at eng/frontend level
    const reactView = resolveScopeBar(tree, "eng/frontend/react")
    expect(reactView.breadcrumbs.map((b) => b.key)).toEqual([SCOPE_ALL, "eng", "eng/frontend"])
    expect(reactView.activeKey).toBe("eng/frontend/react")

    // Selecting standalone repo leaf stays inside virtual Repos level
    const fosView = resolveScopeBar(tree, "fos")
    expect(fosView.breadcrumbs.map((b) => b.key)).toEqual([SCOPE_ALL, SCOPE_REPOS])
    expect(fosView.activeKey).toBe("fos")
    expect(fosView.tabs.map((t) => t.key)).toEqual([SCOPE_REPOS, "fos", "goto"])
  })
})
