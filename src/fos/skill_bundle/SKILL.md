---
name: fos
description: Personal cognitive memory engine (FlowOS) via deterministic CLI. Use at session start to load <=300t L0 redlines, before architecture/ops/deploy decisions to run pre-decision mirror checks, when recalling past decisions or pitfalls, and after bugs or user corrections to record immutable hindsight lessons.
---

# fos (FlowOS)

AI-native cognitive memory CLI backed by an always-on M3 Max brain (`fosd`) with automatic offline cache fallback and outbox sync. Pure CLI — zero MCP overhead.

## Route by Intent

| Goal | Start Here |
|---|---|
| Session-start redlines (<=300t) | `fos boot [--project P] --json` |
| Recall past lessons / decisions | `fos recall "<query>" [--scope P] [--budget 1200] --json` |
| Historical point-in-time slice | `fos recall "<query>" --as-of 2026-08 --json` |
| Pre-decision mirror check | `fos mirror "<proposal>" [--scope P] --json` |
| Verbatim L2 evidence & graph | `fos get <ID> --tier L2 --graph --json` |
| Record pitfall / decision | `fos add --kind lesson\|decision\|thought\|outcome --scope P "<content>" --json` |
| Supersede an old decision | `fos add --kind decision --scope P --supersedes DEC-xxx "<new>" --json` |
| Flush outbox & refresh cache | `fos sync --json` |
| Diagnostics / version / skills | `fos status [--offline] --json` |
| Inspect flags on demand | `fos describe [command]` (JSON schema) |

## Context & Token Discipline

- **Auto-detects project from `cwd`**: omit `--project` on `fos boot` inside a git repo (`--all` overrides).
- **Compact piped JSON**: `--json` automatically emits single-line compact JSON when captured by agents (pretty on TTY).
- **Project only needed fields**: use `--fields id,severity,rule` (implies `--json`) to minimize tokens.
- **Progressive L0 -> L1 -> L2**: start with `boot` (L0) -> `recall` (L1) -> `get --tier L2` only when raw evidence is needed.
- **On-demand discovery**: run `fos describe <cmd>` for exact flags/examples, or read `references/commands.md` for `ingest`, `dream`, `eval`, `undo`, `update`, and exit codes.

## Safety Rules

- Before non-trivial architecture changes, migrations, or production scripts: run `fos mirror "<plan>" --scope <repo> --json` and address any `conflicts` or `lesson_warnings`.
- Never overwrite or delete past decisions; record replacements with `--supersedes <DEC-id>` so bi-temporal history stays intact.
- Never pass raw passwords, private keys, or API tokens to `fos add` (secret redaction is a fallback safety net, not a license).
