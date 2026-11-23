#!/usr/bin/env bash
# Daily private Git backup for FlowOS permanent SQLite state database (`state.db`):
#   - state_backup.sql (deterministic SQL dump of ledger_atoms, eval_cases, proposals,
#     usage_stats, distill_cache, proposal_scan_cache, snapshot pointer & ingest_cursors)
# Never commits rebuildable SQLite binary snapshots, wheels, or credentials.
set -euo pipefail

FOS_HOME="${FOS_HOME:-$HOME/.fos}"
BRAIN_DIR="${FOS_DATA_DIR:-$FOS_HOME/brain}"

mkdir -p "$BRAIN_DIR"

if [ ! -d "$BRAIN_DIR/.git" ]; then
  echo "Initializing git repository in $BRAIN_DIR"
  git -C "$BRAIN_DIR" init -b main
fi

cat > "$BRAIN_DIR/.gitignore" <<'EOF'
# Never commit binary SQLite files, rebuildable snapshots, wheels, or temporary files
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

if [ -f "$BRAIN_DIR/state.db" ]; then
  sqlite3 "$BRAIN_DIR/state.db" .dump > "$BRAIN_DIR/state_backup.sql.tmp"
  mv "$BRAIN_DIR/state_backup.sql.tmp" "$BRAIN_DIR/state_backup.sql"
  git -C "$BRAIN_DIR" add state_backup.sql
fi

# Also keep any legacy text assets tracked if still present
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
