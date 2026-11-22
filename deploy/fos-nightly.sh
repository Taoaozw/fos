#!/usr/bin/env bash
# Nightly FlowOS sleep cycle on M3 Max:
#   1. Ingest verified git fix/revert commits + high-compounding chat taste/decision signals
#   2. Run Quick Dream (LLM distillation + Reflect self-correction + TTL half-life decay + 4-gate eval)
#   3. Back up immutable ledger/ & eval/ to private git repo
set -euo pipefail

export PATH="/Users/clive/.local/bin:/Users/clive/.bun/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export FOS_HOME="${FOS_HOME:-$HOME/.fos}"
GIT_ROOT="${FOS_GIT_ROOT:-$HOME/project/clive}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== [$(date -Iseconds)] FlowOS Nightly Sleep Cycle ==="

if [ -d "$GIT_ROOT" ]; then
  fos ingest --from docs --root "$GIT_ROOT" --limit 30 --json || true
  fos ingest --from git --root "$GIT_ROOT" --since 48h --limit 30 --json || true
fi
fos ingest --from docs --limit 20 --json || true
fos ingest --from all --since 48h --limit 25 --json || true

# Run LLM-distilled dream + Reflect + TTL decay + 4-dimension Regression Gate
if ! fos dream --reasoner cli --json; then
  echo "[warn] LLM dream did not promote (gate blocked or CLI unavailable); keeping current snapshot untouched." >&2
fi

if [ -x "$SCRIPT_DIR/fos-backup.sh" ]; then
  "$SCRIPT_DIR/fos-backup.sh" || true
fi

echo "=== [$(date -Iseconds)] Done ==="
