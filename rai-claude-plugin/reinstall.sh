#!/usr/bin/env bash
# Re-installs the rai-claude-plugin into the current Python environment
# and prints the Claude Code slash commands needed to register it.

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing Python dependencies..."
pip3 install --upgrade httpx "mcp>=1.0.0" pynacl

echo ""
echo "==> Dependencies installed."
echo ""
echo "Now run these two commands inside Claude Code to register the plugin:"
echo ""
echo "  /plugin marketplace add ${PLUGIN_DIR}"
echo "  /plugin install rai@rai"
echo ""
echo "Then restart Claude Code so the /rai:* commands register."
