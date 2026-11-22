import type {
  BootResponse,
  DashboardResponse,
  DreamResponse,
  EvalResponse,
  HealthResponse,
  MirrorResponse,
  NodeDetailResponse,
  ProposalScanResponse,
  ProposalsListResponse,
  RecallResponse,
} from "./types"

const TOKEN_STORAGE_KEY = "fos_bearer_token"

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export function getStoredToken(): string {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY) ?? ""
  } catch {
    return ""
  }
}

export function setStoredToken(token: string): void {
  try {
    if (token) {
      localStorage.setItem(TOKEN_STORAGE_KEY, token)
    } else {
      localStorage.removeItem(TOKEN_STORAGE_KEY)
    }
  } catch {
    // ignore storage errors
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" }
  const token = getStoredToken()
  if (token) headers["Authorization"] = `Bearer ${token}`
  if (body !== undefined) headers["Content-Type"] = "application/json"

  const resp = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const payload = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    const detail = (payload as { detail?: { error?: string } | string }).detail
    const msg =
      typeof detail === "string"
        ? detail
        : detail?.error ?? `HTTP ${resp.status}`
    throw new ApiError(msg, resp.status)
  }
  return payload as T
}

export const fosApi = {
  health: () => request<HealthResponse>("GET", "/v1/health"),
  dashboard: (ledgerLimit = 80) =>
    request<DashboardResponse>("GET", `/v1/dashboard?ledger_limit=${ledgerLimit}`),
  boot: (project?: string, budget = 300) => {
    const qs = new URLSearchParams({ budget: String(budget) })
    if (project) qs.set("project", project)
    return request<BootResponse>("GET", `/v1/boot?${qs.toString()}`)
  },
  getNode: (id: string, tier: "L0" | "L1" | "L2" = "L2", graph = true) =>
    request<NodeDetailResponse>(
      "GET",
      `/v1/nodes/${encodeURIComponent(id)}?tier=${tier}&graph=${graph}`
    ),
  addAtom: (input: {
    kind: string
    scope: string
    content: string
    severity?: string
    supersedes?: string
  }) =>
    request<{ atom: { id: string }; node?: { id: string } }>("POST", "/v1/atoms", {
      kind: input.kind,
      scope: input.scope,
      content: input.content,
      meta: {
        ...(input.severity ? { severity: input.severity } : {}),
        ...(input.supersedes ? { supersedes: input.supersedes } : {}),
      },
    }),
  correctNode: (id: string, content: string, reason: string) =>
    request<{ superseded: string; node: { id: string } }>(
      "POST",
      `/v1/nodes/${encodeURIComponent(id)}/correct`,
      { content, reason }
    ),
  archiveNode: (id: string, reason: string) =>
    request<{ archived: string }>(
      "POST",
      `/v1/nodes/${encodeURIComponent(id)}/archive`,
      { reason }
    ),
  pinNode: (id: string) =>
    request<{ pinned_node: string }>("POST", `/v1/eval/pin/${encodeURIComponent(id)}`),
  feedbackNode: (id: string, verdict: "useful" | "noise", context = "") =>
    request<{ node_id: string; utility_score: number }>(
      "POST",
      `/v1/nodes/${encodeURIComponent(id)}/feedback`,
      { verdict, context }
    ),
  graduateNode: (id: string, guardPattern?: string, reason = "") =>
    request<{ graduated: string }>(
      "POST",
      `/v1/nodes/${encodeURIComponent(id)}/graduate`,
      { guard_pattern: guardPattern || null, reason }
    ),
  recall: (input: {
    q: string
    scope?: string
    asOf?: string
    budget?: number
    cwdStack?: string
  }) => {
    const qs = new URLSearchParams({
      q: input.q,
      budget: String(input.budget ?? 1200),
    })
    if (input.scope) qs.set("scope", input.scope)
    if (input.asOf) qs.set("as_of", input.asOf)
    if (input.cwdStack) qs.set("cwd_stack", input.cwdStack)
    return request<RecallResponse>("GET", `/v1/recall?${qs.toString()}`)
  },
  mirror: (proposal: string, scope?: string) =>
    request<MirrorResponse>("POST", "/v1/mirror", {
      proposal,
      scope: scope || null,
    }),
  runEval: (suite = "all") =>
    request<EvalResponse>("POST", "/v1/eval", { suite }),
  runDream: (dryRun = false) =>
    request<DreamResponse>("POST", "/v1/dream", { dry_run: dryRun, no_gate: false }),
  undo: () =>
    request<{ previous: string; current: string }>("POST", "/v1/undo"),
  listProposals: (status = "pending") =>
    request<ProposalsListResponse>(
      "GET",
      `/v1/proposals?status=${encodeURIComponent(status)}`
    ),
  scanProposals: (input?: { reasoner?: string; force?: boolean; rootDir?: string }) =>
    request<ProposalScanResponse>("POST", "/v1/proposals/scan", {
      root_dir: input?.rootDir || null,
      reasoner: input?.reasoner || null,
      force: Boolean(input?.force),
    }),
  acceptProposal: (
    id: string,
    input?: {
      rule?: string
      rationale?: string
      guard_hint?: string
      severity?: string
      trigger_when?: string[]
      force?: boolean
    }
  ) =>
    request<{ result_node_id: string | null }>(
      "POST",
      `/v1/proposals/${encodeURIComponent(id)}/accept`,
      input ?? {}
    ),
  dismissProposal: (id: string, reason = "") =>
    request<{ proposal: { id: string } }>(
      "POST",
      `/v1/proposals/${encodeURIComponent(id)}/dismiss`,
      { reason }
    ),
}
