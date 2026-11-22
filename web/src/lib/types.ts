export type NodeKind = "principle" | "lesson" | "decision" | "thought" | "outcome"
export type NodeStatus = "active" | "superseded" | "archived"
export type Severity = "P0" | "P1" | "P2"

export interface HealthResponse {
  status: string
  version: string
  cli_version: string
  snapshot_version: string | null
  auth_enabled: boolean
}

export interface CompiledNode {
  id: string
  type: NodeKind | string
  scope: string
  status: NodeStatus | string
  severity: Severity | string
  valid_from: string
  valid_until: string | null
  superseded_by: string | null
  seen_count: number
  seen_dates: string[]
  confidence: number
  trigger_when: string[]
  rule: string
  rationale: string
  guard_hint: string
  entities: string[]
  contradicts: string[]
  evidence_ids: string[]
  l0: string
  created_at?: string
  updated_at?: string
  pinned?: boolean
}

export interface GraphEdge {
  src_id: string
  dst_id: string
  edge_type: string
  valid_from: string
  valid_until: string | null
  meta: Record<string, unknown>
  created_at: string
}

export interface LedgerAtom {
  id: string
  ts: string
  device: string
  kind: string
  scope: string
  source: string
  raw: string
  meta: Record<string, unknown>
}

export interface DashboardResponse {
  snapshot_version: string | null
  history: string[]
  rejected_snapshots: string[]
  sqlite_vec: boolean
  meta: Record<string, string>
  counts: {
    total_nodes: number
    active_nodes: number
    superseded_nodes: number
    archived_nodes: number
    ledger_atoms: number
    golden_cases: number
    pinned_nodes: number
    edges: number
  }
  distributions: {
    by_status: Record<string, number>
    by_type: Record<string, number>
    by_scope: Record<string, number>
    by_severity: Record<string, number>
  }
  scopes: string[]
  nodes: CompiledNode[]
  edges: GraphEdge[]
  recent_atoms: LedgerAtom[]
}

export interface NodeDetailResponse {
  mode: string
  snapshot_version: string
  tier: string
  id: string
  l0: string
  node: CompiledNode
  evidence?: LedgerAtom[]
  graph?: GraphEdge[]
}

export interface BootResponse {
  mode: string
  snapshot_version: string
  project: string | null
  budget_tokens: number
  used_tokens: number
  count: number
  kernel_text: string
  entries: Array<{
    id: string
    type: string
    scope: string
    severity: string
    seen_count: number
    l0: string
  }>
}

export interface RecallItem extends CompiledNode {
  score: number
  channels: string[]
}

export interface RecallResponse {
  mode: string
  snapshot_version: string
  query: string
  scope: string | null
  kind: string | null
  as_of: string | null
  budget_tokens: number
  used_tokens: number
  count: number
  results: RecallItem[]
}

export interface MirrorResponse {
  mode: string
  snapshot_version: string
  proposal: string
  scope: string | null
  conflicts: Array<{ id: string; type: string; severity: string; reason: string; rule: string; matched_terms?: string[] }>
  lesson_warnings: Array<{ id: string; severity: string; seen_count: number; rule: string; rationale: string }>
  prior_decisions: Array<{ id: string; status: string; valid_from: string; valid_until?: string | null; superseded_by?: string | null; rule: string; rationale: string }>
  blind_spots: Array<{ kind: string; detail: string }>
}

export interface EvalGateMetrics {
  passed: boolean
  cases?: number
  checked_nodes?: number
  recall_at_5?: number
  mrr?: number
  pinned_top3_rate?: number
  stale_leaks?: number
  as_of_restore_rate?: number
  retention_rate?: number
  guard_hit_rate?: number
  failures?: unknown[]
}

export interface EvalResponse {
  passed: boolean
  snapshot_version: string
  suite: string
  golden_cases: number
  regressions: string[]
  gates: Record<string, EvalGateMetrics>
}

export interface DreamResponse {
  promoted: boolean
  dry_run: boolean
  baseline_version: string
  current_version?: string
  candidate_version?: string
  stats: Record<string, unknown>
  gate?: EvalResponse
}
