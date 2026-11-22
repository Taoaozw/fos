#!/bin/sh
# FlowOS (fos) one-line client installer for macOS / Linux (learned from fops).
#   curl -fsSL {{BRAIN}}/install.sh | sh -s -- TOKEN
# Downloads the SHA-256 verified CLI wheel from the M3 Max brain, installs via uv,
# binds this device, syncs the initial offline cache, and installs the AI skill.
set -e

BRAIN='{{BRAIN}}'
TOKEN="$1"
if [ -z "$TOKEN" ]; then
  echo "usage: curl -fsSL $BRAIN/install.sh | sh -s -- TOKEN" >&2
  exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
WHEEL="$TMP_DIR/fos-latest-py3-none-any.whl"

echo "Downloading fos CLI wheel from $BRAIN ..."
curl -fsSL -H "Authorization: Bearer $TOKEN" "$BRAIN/v1/cli/download" -o "$WHEEL"

echo "Installing fos via uv tool..."
uv tool install --force --reinstall "$WHEEL"

export PATH="$HOME/.local/bin:$PATH"
fos init --brain "$BRAIN" --token "$TOKEN"
fos sync
fos skill install || echo "(no AI tool directory detected; run 'fos skill install --dir DIR' later)"
echo
fos status
echo
echo "Done. Try: fos boot --json | later: fos sync / fos update"
