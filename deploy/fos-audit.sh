#!/usr/bin/env bash
# Scheduled FlowOS Codebase Memory Audit on M3 Max:
#   - Mon-Sat: audits owned repos that have active memories AND git commits in the last 48h
#   - Sunday (or `--all`): full audit across ALL owned repos with active memories
#   - Emits `retire_stale` (obsolete/conflicting/guarded) and `fix_drifted` (distilled/reclassified/priority-tuned)
#     proposals into the Proposal Inbox for review; refreshes `verified_at` on verified cards.
set -euo pipefail

export PATH="/Users/clive/.local/bin:/Users/clive/.bun/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export FOS_HOME="${FOS_HOME:-$HOME/.fos}"
GIT_ROOT="${FOS_GIT_ROOT:-$HOME/project/clive}"
MODEL="${FOS_AUDIT_MODEL:-gpt-6.2-sol}"
EFFORT="${FOS_AUDIT_EFFORT:-medium}"
SINCE_HOURS="${FOS_AUDIT_SINCE_HOURS:-48}"
CONCURRENCY="${FOS_AUDIT_CONCURRENCY:-2}"
TIMEOUT_S="${FOS_AUDIT_TIMEOUT:-420}"

FORCE_ALL=0
EXTRA_FLAGS=()
for arg in "$@"; do
  case "$arg" in
    --all) FORCE_ALL=1 ;;
    --dry-run|--json) EXTRA_FLAGS+=("$arg") ;;
    *) echo "Usage: $0 [--all] [--dry-run] [--json]" >&2; exit 2 ;;
  esac
done

DOW="$(date +%u)"
if [ "$DOW" = "7" ]; then
  FORCE_ALL=1
fi

echo "=== [$(date -Iseconds)] FlowOS Scheduled Audit (model=$MODEL effort=$EFFORT concurrency=$CONCURRENCY force_all=$FORCE_ALL) ==="

AUDIT_ARGS=(
  audit
  --all
  --root "$GIT_ROOT"
  --concurrency "$CONCURRENCY"
  --model "$MODEL"
  --effort "$EFFORT"
  --timeout "$TIMEOUT_S"
)
if [ "$FORCE_ALL" -eq 0 ]; then
  AUDIT_ARGS+=(--since-hours "$SINCE_HOURS")
fi
if [ "${#EXTRA_FLAGS[@]}" -gt 0 ]; then
  AUDIT_ARGS+=("${EXTRA_FLAGS[@]}")
fi

fos "${AUDIT_ARGS[@]}"

echo "=== [$(date -Iseconds)] Audit Finished ==="
