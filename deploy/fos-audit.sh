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

echo "=== [$(date -Iseconds)] FlowOS Scheduled Audit (model=$MODEL effort=$EFFORT force_all=$FORCE_ALL) ==="

# Read active non-global scopes directly from current snapshot via fos Python environment
FOS_PY="$(head -n 1 "$(command -v fos)" | sed 's/^#!//')"
SCOPES="$("$FOS_PY" - <<'PY'
import json, sqlite3
from pathlib import Path
import os

brain = Path(os.environ.get("FOS_HOME", Path.home() / ".fos")) / "brain"
cur = json.loads((brain / "current.json").read_text())["current"]
db = sqlite3.connect(str(brain / "snapshots" / f"{cur}.db"))
for (scope,) in db.execute(
    "SELECT DISTINCT scope FROM nodes WHERE status='active' AND scope != 'global' ORDER BY scope"
).fetchall():
    print(scope)
PY
)"

if [ -z "$SCOPES" ]; then
  echo "[info] No active repo scopes to audit."
  exit 0
fi

AUDITED=0
SKIPPED=0
FAILED=0

while IFS= read -r scope; do
  [ -z "$scope" ] && continue
  repo_dir="$GIT_ROOT/$scope"
  if [ ! -d "$repo_dir/.git" ]; then
    echo "[skip] $scope: local git repo not found at $repo_dir"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  if [ "$FORCE_ALL" -eq 0 ]; then
    recent="$(git -C "$repo_dir" log --since="${SINCE_HOURS} hours ago" -n 1 --oneline 2>/dev/null || true)"
    if [ -z "$recent" ]; then
      echo "[skip] $scope: no git commits in last ${SINCE_HOURS}h"
      SKIPPED=$((SKIPPED + 1))
      continue
    fi
  fi

  echo "--- Auditing scope=$scope ($repo_dir) ---"
  if fos audit --scope "$scope" --repo "$repo_dir" --model "$MODEL" --effort "$EFFORT" --timeout "$TIMEOUT_S" ${EXTRA_FLAGS[@]+"${EXTRA_FLAGS[@]}"}; then
    AUDITED=$((AUDITED + 1))
  else
    echo "[warn] Audit failed for scope=$scope; continuing remaining repos." >&2
    FAILED=$((FAILED + 1))
  fi
done <<< "$SCOPES"

echo "=== [$(date -Iseconds)] Audit Finished (audited=$AUDITED skipped=$SKIPPED failed=$FAILED) ==="
