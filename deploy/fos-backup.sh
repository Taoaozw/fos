#!/usr/bin/env bash
# Daily private Git backup for FlowOS permanent & high-value text assets:
#   - ledger/ (immutable append-only source of truth)
#   - eval/ (golden regression assertions)
#   - proposals.jsonl (governance inbox & dismissal cooldowns)
#   - usage_stats.json (PlugMem recall utility telemetry)
#   - distill_cache.json & proposal_scan_cache.json (zero-cost rebuild caches)
# Never commits rebuildable SQLite snapshots, wheels, or credentials.
set -euo pipefail

FOS_HOME="${FOS_HOME:-$HOME/.fos}"
BRAIN_DIR="${FOS_DATA_DIR:-$FOS_HOME/brain}"

mkdir -p "$BRAIN_DIR"

if [ ! -d "$BRAIN_DIR/.git" ]; then
  echo "Initializing git repository in $BRAIN_DIR"
  git -C "$BRAIN_DIR" init -b main
fi

cat > "$BRAIN_DIR/.gitignore" <<'EOF'
# Never commit rebuildable SQLite snapshots, wheels, or temporary files
snapshots/
dist/
current.json
*.db
*.db-wal
*.db-shm
*.tmp
*.whl
EOF

git -C "$BRAIN_DIR" add .gitignore
for item in ledger eval proposals.jsonl usage_stats.json distill_cache.json proposal_scan_cache.json; do
  if [ -e "$BRAIN_DIR/$item" ]; then
    git -C "$BRAIN_DIR" add "$item"
  fi
done

if git -C "$BRAIN_DIR" diff --cached --quiet; then
  echo "No new FlowOS brain changes to back up."
  exit 0
fi

STAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
git -C "$BRAIN_DIR" commit -m "chore(fos): backup brain ledger & state ($STAMP)"

if git -C "$BRAIN_DIR" remote get-url origin >/dev/null 2>&1; then
  git -C "$BRAIN_DIR" push -u origin HEAD
  echo "Pushed FlowOS backup ($STAMP) to origin."
else
  echo "Committed local backup ($STAMP). Add a private git remote 'origin' to enable remote push."
fi
