#!/bin/bash
# Somnia entrypoint - fix volume permissions, then run services as somnia user

set -e

echo "Starting Somnia services..."
echo "  APP_DIR: ${SOMNIA_APP_DIR:-/app}"
echo "  DATA_DIR: ${SOMNIA_DATA_DIR:-/data/somnia}"
echo "  API port: ${SOMNIA_PORT:-8010}"
echo "  MCP port: ${SOMNIA_MCP_PORT:-8011}"

# Fix volume permissions (runs as root before dropping privileges)
# Named Docker volumes may be owned by root from previous builds
if [ "$(id -u)" = "0" ]; then
    echo "  Fixing data directory permissions..."
    mkdir -p /data/somnia/backups /data/somnia/solo-work /data/somnia/logs /data/somnia/db
    chown -R somnia:somnia /data/somnia
    # Belt-and-suspenders: explicit chmod on writable directories
    # Synology bind mounts can ignore chown due to ACLs
    chmod -R u+rwX /data/somnia
    chmod 777 /data/somnia/backups /data/somnia/solo-work /data/somnia/logs
    # Ensure continuity_note.md is writable if it exists
    [ -f /data/somnia/continuity_note.md ] && chmod 666 /data/somnia/continuity_note.md
    chown -R somnia:somnia /home/somnia

    echo "  Dropping to somnia user..."
    exec gosu somnia "$0" "$@"
fi

# Everything below runs as somnia user

# ── Bootstrap Claude Code agents and MCP config ────────────────────────
AGENTS_SRC="/app/agents"
CLAUDE_DIR="${HOME}/.claude"
if [ -d "$AGENTS_SRC" ]; then
    mkdir -p "${CLAUDE_DIR}/agents"
    cp ${AGENTS_SRC}/*.md "${CLAUDE_DIR}/agents/" 2>/dev/null
    echo "  Quies agents installed: $(ls ${CLAUDE_DIR}/agents/*.md 2>/dev/null | wc -l) agents"
fi

# Register MCP servers via CLI so they appear in settings.json (user scope).
# The old approach of copying .mcp.json doesn't work — that's project-level
# config and agents run in /tmp. claude mcp add --scope user writes to
# ~/.claude/settings.json which is always visible.
if [ -f "${AGENTS_SRC}/mcp.json" ]; then
    echo "  Registering MCP servers..."
    for name in $(python3 -c "import json; print(' '.join(json.load(open('${AGENTS_SRC}/mcp.json')).keys()))"); do
        url=$(python3 -c "import json; print(json.load(open('${AGENTS_SRC}/mcp.json'))['${name}']['url'])")
        claude mcp add --transport http --scope user "$name" "$url" 2>/dev/null && \
            echo "    ✓ ${name} → ${url}" || \
            echo "    ✗ ${name} failed"
    done
fi

# Start the Flask API in the background
python daemon/somnia_daemon.py &
API_PID=$!
echo "  Flask API started (PID: $API_PID)"

# Wait briefly for API to be ready
sleep 2

# Start the MCP server in the foreground
python mcp/somnia_mcp.py &
MCP_PID=$!
echo "  MCP server started (PID: $MCP_PID)"

# Wait for either process to exit
wait -n $API_PID $MCP_PID

# If either exits, kill the other and exit
echo "A service exited, shutting down..."
kill $API_PID $MCP_PID 2>/dev/null
wait
