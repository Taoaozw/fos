#!/usr/bin/env bash
# One-command FlowOS brain restore on a freshly installed Mac.
# Usage: ./deploy/fos-restore.sh https://github.com/Taoaozw/fos-brain.git
set -euo pipefail

BACKUP_REPO="${1:-https://github.com/Taoaozw/fos-brain.git}"
FOS_HOME="${FOS_HOME:-$HOME/.fos}"
BRAIN_DIR="${FOS_DATA_DIR:-$FOS_HOME/brain}"

if [ -f "$BRAIN_DIR/state.db" ] || { [ -d "$BRAIN_DIR/ledger" ] && [ -n "$(ls -A "$BRAIN_DIR/ledger" 2>/dev/null || true)" ]; }; then
  echo "Existing FlowOS state detected at $BRAIN_DIR. Aborting to prevent overwrite." >&2
  exit 1
fi

mkdir -p "$FOS_HOME"
rm -rf "$BRAIN_DIR"
echo "Cloning private FlowOS backup from $BACKUP_REPO -> $BRAIN_DIR ..."
git clone "$BACKUP_REPO" "$BRAIN_DIR"

if [ -f "$BRAIN_DIR/state_backup.sql" ] && [ ! -f "$BRAIN_DIR/state.db" ]; then
  echo "Restoring SQLite state.db from state_backup.sql..."
  sqlite3 "$BRAIN_DIR/state.db" < "$BRAIN_DIR/state_backup.sql"
fi

echo "Rebuilding SQLite snapshot from immutable ledger..."
fos dream --reasoner cli --json || fos rebuild --json

echo "Installing AI agent skills..."
fos skill install

echo "Restore complete! Next: run 'fosd init' (if new host) and load deploy/com.clive.fos*.plist."
