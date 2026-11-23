import {
  parseAsBoolean,
  parseAsString,
  parseAsStringLiteral,
} from "nuqs"
import { SCOPE_ALL } from "@/lib/scope-tree"

export const APP_TABS = [
  "overview",
  "proposals",
  "cards",
  "graph",
  "playground",
  "audit",
] as const
export type AppTab = (typeof APP_TABS)[number]

export const CARD_STATUSES = ["active", "__all__", "superseded", "archived"] as const
export type CardStatusFilter = (typeof CARD_STATUSES)[number]

export const CARD_SEVERITIES = ["__all__", "P0", "P1", "P2"] as const
export type CardSeverityFilter = (typeof CARD_SEVERITIES)[number]

export const CARD_KINDS = [
  "__all__",
  "principle",
  "lesson",
  "decision",
  "thought",
  "outcome",
] as const
export type CardKindFilter = (typeof CARD_KINDS)[number]

export const CARD_SPECIALS = ["all", "patterns", "issues", "guards"] as const
export type CardSpecialFilter = (typeof CARD_SPECIALS)[number]

export const GRAPH_MODES = ["all", "connected"] as const
export type GraphViewMode = (typeof GRAPH_MODES)[number]

export const PROPOSAL_STATUSES = [
  "pending",
  "accepted",
  "dismissed",
  "expired",
  "all",
] as const
export type ProposalStatusFilter = (typeof PROPOSAL_STATUSES)[number]

export const PROPOSAL_KINDS = [
  "all",
  "fix_drifted",
  "promote_global",
  "merge_dedup",
  "retire_stale",
] as const
export type ProposalKindFilter = (typeof PROPOSAL_KINDS)[number]

/** Global navigation & cross-panel selected card URL params. */
export const appNavParsers = {
  tab: parseAsStringLiteral(APP_TABS).withDefault("overview"),
  node: parseAsString,
}

/** Knowledge Cards filter URL params (single source of truth). */
export const cardsFilterParsers = {
  q: parseAsString.withDefault(""),
  scope: parseAsString.withDefault(SCOPE_ALL),
  status: parseAsStringLiteral(CARD_STATUSES).withDefault("active"),
  severity: parseAsStringLiteral(CARD_SEVERITIES).withDefault("__all__"),
  kind: parseAsStringLiteral(CARD_KINDS).withDefault("__all__"),
  special: parseAsStringLiteral(CARD_SPECIALS).withDefault("all"),
}

/** Bi-temporal Knowledge Graph filter URL params. */
export const graphFilterParsers = {
  gq: parseAsString.withDefault(""),
  gscope: parseAsString.withDefault(SCOPE_ALL),
  gmode: parseAsStringLiteral(GRAPH_MODES).withDefault("all"),
  ghistory: parseAsBoolean.withDefault(true),
}

/** Proposals Inbox filter URL params. */
export const proposalsFilterParsers = {
  pstatus: parseAsStringLiteral(PROPOSAL_STATUSES).withDefault("pending"),
  pkind: parseAsStringLiteral(PROPOSAL_KINDS).withDefault("all"),
}

/** Audit Ledger search URL param. */
export const auditSearchParser = parseAsString.withDefault("")
