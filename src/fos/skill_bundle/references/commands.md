# fos Command Reference

Read this file on demand for evolution workflows (`ingest`, `dream`, `eval`, `undo`), client sync/self-update, and exit-code contracts. For exact flag schemas of any command, run `fos describe <command>`.

## 1. Memory Retrieval & Inspection

```bash
fos boot [--project|--scope P | --all] [--scopes S1,S2] [--budget 300] [--fields id,severity,l0] [--json]
fos recall "<query>" [--kind lesson|decision|thought|outcome|principle] [--scope P] [--scopes S1,S2] [--as-of 2026-08] [--budget 1200] [--fields id,rule] [--json]
fos get <ID> [--tier L0|L1|L2] [--graph] [--json]
fos mirror "<proposal>" [--scope P] [--scopes S1,S2] [--json]
```

Short aliases: `b` (`boot`), `rec`/`r` (`recall`), `g` (`get`), `mir`/`check` (`mirror`), `a` (`add`), `cor`/`fix` (`correct`), `arc`/`retire` (`archive`), `s` (`sync`), `st` (`status`), `desc` (`describe`), `dr` (`dream`), `ev` (`eval`), `sk` (`skill`), `up` (`update`).

## 2. Capture, Self-Correction, Archival & Passive Hindsight Ingestion

```bash
# Record explicit lesson, decision, thought, outcome, or principle
fos add --kind lesson --scope fops --severity P0 "Never use rsync --mkpath on macOS; run ssh mkdir -p first" --json
fos add --kind decision --scope fos --supersedes DEC-001 "Use SQLite WAL + sqlite-vec" --json

# Self-correct an inaccurate rule or retire an obsolete/guarded one (immutable ledger, --as-of preserved)
fos correct LSN-005 "Preserve decimal precision for upstream usage/quota fields" --reason "refined boundary" --json
fos archive LSN-006 --reason "enforced by permanent Guard Test" --json

# Incremental high-compounding hindsight extraction (git fix commits + user taste/decisions)
fos ingest --from all --since 7d --json
fos ingest --from git --root ~/project/clive --since 14d --json
fos ingest --from claude --since 24h --json
fos ingest --from codex --since 24h --json
fos ingest --from pi --since 24h --json
fos ingest --from antigravity --since 24h --json
fos ingest --from fops --since 24h --json
```

## 3. Sleep Compilation, Regression Gates & Rollback (M3 Max Brain)

```bash
fos dream [--dry-run] [--no-gate] [--export-songe ./songe.jsonl] --json
fos eval [--suite all|recall|temporal|fidelity|behavior] --json
fos eval pin <ID> --json
fos undo --json
fos rebuild --json
fos export --format md --out ./views/ --json
```

## 4. Multi-Device Sync, Status, Skills & Auto-Update

```bash
# First-time portable device setup (or via one-line curl installer from brain)
fos init --brain http://<m3max-tailscale-ip>:7622 --token <TOKEN>
fos sync --json

# Diagnostics, skill refresh, and self-update
fos status [--offline] --json
fos skill install [--dir DIR] [--refresh-only] --json
fos update [--check] [--json]

# Remote device scheduled session/git ingest (run on portable Mac / Linux client; brain runs nightly dream centrally)
fos ingest --from all --since 48h --limit 25 --json
fos ingest --from git --root ~/projects --since 48h --limit 30 --json
fos sync --json

# Unbind remote brain (flush outbox, stop client ingest job, clear token & cache; keeps CLI)
fos sync --json || true
launchctl unload ~/Library/LaunchAgents/com.clive.fos-client-ingest.plist 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.clive.fos-client-ingest.plist ~/.fos/bin/fos-client-ingest.sh
(crontab -l 2>/dev/null | grep -v '# fos-client-ingest' | crontab -) 2>/dev/null || true
rm -rf ~/.fos/config.json ~/.fos/cache

# Full client uninstall (in addition to unbind above)
rm -rf ~/.gemini/config/skills/fos ~/.claude/skills/fos ~/.codex/skills/fos ~/.agents/skills/fos ~/.cursor/skills/fos ~/.pi/agent/skills/fos
uv tool uninstall fos || true
rm -rf ~/.fos
```

- When the brain advertises a newer CLI wheel (`X-Fos-Version`), `fos` silently triggers a debounced background self-update (`FOS_NO_AUTOUPDATE=1` disables it), verifies SHA-256, and automatically refreshes installed `SKILL.md` files via `fos skill install --refresh-only`.

## 5. Machine-Readable Output & Exit Codes

- `--json` emits single-line compact JSON when piped/captured, and pretty JSON on TTY (override with `FOS_COMPACT_JSON=1` or `FOS_PRETTY_JSON=1`).
- On error with `--json`, `stderr` emits `{"ok":false,"code":"...","error":"...","hint":"..."}`.
- Semantic exit codes:
  - `0`: success
  - `1`: runtime / network / auth error
  - `2`: invalid usage or flags
  - `3`: target not found (`get`, `describe`, `undo`, `eval pin`)
  - `4`: regression gate failed (`dream`, `eval`)
  - `130`: interrupted (`Ctrl-C`)
