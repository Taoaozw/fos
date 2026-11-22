#!/usr/bin/env bash
# Daily private Git backup for immutable FlowOS permanent assets (ledger/ and eval/ ONLY).
set -euo pipefail

FOS_HOME="${FOS_HOME:-$HOME/.fos}"
BRAIN_DIR="${FOS_DATA_DIR:-$FOS_HOME/brain}"

if [ ! -d "$BRAIN_DIR/.git" ]; then
  echo "Initializing git repository in $BRAIN_DIR"
  git -C "$BRAIN_DIR" init
  cat > "$BRAIN_DIR/.gitignore" <<'EOF'
# Never commit rebuildable SQLite snapshots or temporary files
snapshots/
current.json
*.db
*.db-wal
*.db-shm
*.tmp
EOF
fi

git -C "$BRAIN_DIR" add .gitignore ledger/ eval/
if git -C "$BRAIN_DIR" diff --cached --quiet; then
  echo "No new ledger/eval changes to back up."
  exit 0
fi

STAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
git -C "$BRAIN_DIR" commit -m "chore(fos): backup ledger & eval ($STAMP)"

if git -C "$BRAIN_DIR" remote get-url origin >/dev/null 2>&1; then
  git -C "$BRAIN_DIR" push origin HEAD
  echo "Pushed FlowOS backup ($STAMP) to origin."
else
  echo "Committed local backup ($STAMP). Add a private git remote 'origin' to enable remote push."
fi
