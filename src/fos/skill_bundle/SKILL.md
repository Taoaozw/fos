---
name: fos
description: Personal cognitive memory engine (FlowOS) via deterministic CLI. Use at session start to load <=300t L0 redlines, before architecture/ops/deploy decisions to run pre-decision mirror checks, when recalling past decisions or pitfalls, after bugs or user corrections to record immutable hindsight lessons, and whenever an existing memory is found wrong or obsolete to self-correct or archive it.
---

# fos (FlowOS)

AI-native cognitive memory CLI backed by an always-on M3 Max brain (`fosd`) with automatic offline cache fallback and outbox sync. Pure CLI — zero MCP overhead.

## Route by Intent

| Goal | Start Here |
|---|---|
| Session-start redlines (<=300t) | `fos boot [--project P]` |
| Recall past lessons / decisions | `fos recall "<query>" [--scope P] [--budget 900]` |
| Historical point-in-time slice | `fos recall "<query>" --as-of 2026-08` |
| Pre-decision mirror check | `fos mirror "<proposal>" [--scope P]` |
| Verbatim L2 evidence & graph | `fos get <ID> --tier L2 --graph --json` |
| Record pitfall / decision | `fos add --kind lesson\|decision\|principle --scope P "<content>" --json` |
| Mark recall useful / noise | `fos feedback <ID> --useful\|--noise` |
| Save / evolve code pattern | `fos pattern add <file[::symbol]> "<invariant>" [--supersedes ID] --why "<benefit>" --bench "<cmd>" --json` |
| Sync drifted code pattern | `fos pattern sync <ID> [--why "<fix or perf gain>"] --json` |
| Self-correct wrong/outdated rule | `fos correct <ID> "<refined rule>" --reason "<why>" --json` |
| Retire obsolete/guarded rule | `fos archive <ID> --reason "<why>" --json` |
| Audit repo memories vs live code | `fos audit [--scope P] [--model gpt-6.2-sol] [--effort medium] [--dry-run]` |
| Flush outbox & refresh cache | `fos sync --json` |
| Diagnostics / version / skills | `fos status [--offline] --json` |
| Inspect flags on demand | `fos describe [command]` (JSON schema) |

## Context & Token Discipline

- **Prefer default compact text for reads**: `fos boot`, `fos recall`, and `fos mirror` emit dense TOON-style `[ID|SEV] rule` lines (~65% fewer tokens than JSON). Add `--json` or `--fields id,severity,rule` only when programmatically parsing output.
- **Auto-detects project & stack profile from `cwd`**: omit `--project` on `fos boot` inside a git repo (`--all` overrides); `fos` automatically mounts matching domain/stack branches (`eng/frontend/*`, `eng/backend/*`, `eng/ops`) and excludes repo-root `AGENTS.md`/`CLAUDE.md` duplicates from L0.
- **Hierarchical scopes (<=3 levels, Least Upper Bound)**: reserve `global` for thin cross-industry meta-principles; scope reusable rules to `eng`, `eng/frontend[/stack]`, `eng/backend[/stack]`, `eng/ops`, `eng/ai`, `product`, `writing`, etc., and put cross-cutting facets (`security`, `perf`, `migration`) in `--entities`.
- **Avoid re-fetching L0 rules**: pass `--exclude ID1,ID2` on `fos recall` when those cards are already complete in your L0 boot context.
- **Progressive L0 -> L1 -> L2**: start with `boot` (L0) -> `recall` (L1) -> `get --tier L2` only when raw evidence is needed.
- **On-demand discovery**: run `fos describe <cmd>` for exact flags/examples, or read `references/commands.md` for `ingest`, `dream`, `eval`, `undo`, `update`, and exit codes.

## Safety & Hygiene Rules

- **Critically filter every recalled card**: `fos boot`, `fos recall`, and `fos mirror` return candidate context, not blind truth. Verify applicability against the current repo and task before following; ignore irrelevant hits rather than forcing them in.
- **Close the self-cleaning feedback loop**: whenever a recalled card is off-topic, generic fluff, or misleading noise, mark `fos feedback <ID> --noise` (and `--useful` when it prevents a real bug or steers a decision) so PlugMem automatically demotes noisy cards and surfaces them for retirement.
- Before non-trivial architecture changes, migrations, or production scripts: run `fos mirror "<plan>" --scope <repo> --json` and address any genuine `conflicts` or `lesson_warnings`.
- **Self-correct aggressively**: if a recalled rule is contradicted by current code/tests, run `fos correct <ID> "<new rule>" --reason "..."`; if obsolete, generic etiquette, or already enforced by a permanent Guard Test, run `fos archive <ID> --reason "..."`.
- **Code patterns are baselines, not gospel**: when recalling a `code_exemplar`, critically review the L2 skeleton / `source_ref` before reusing it — check for subtle leaks, races, or cleaner native APIs in newer framework versions (especially when `compat_check.status == "major_drift"` or `freshness == "drifted"`).
  - If the old pattern has a real bug or is now an anti-pattern: never silently delete it — demote it into a warning lesson via `fos add --kind lesson --supersedes <ID> "<what broke & what to do instead>"` and mark `fos feedback <ID> --noise`.
  - If you fixed or further optimized it: evolve the baseline via `fos pattern sync <ID> --why "..."` (in-place) or `fos pattern add <new_file::sym> "..." --supersedes <ID>` (new repo/symbol), and record `fos feedback <ID> --useful` once verified.
- **6-Month Half-Life**: only record durable principles, engineering pitfalls, or architectural trade-offs — never one-off ticket/account trivia.
- Never pass raw passwords, private keys, or API tokens to `fos add` (secret redaction is a fallback safety net, not a license).
