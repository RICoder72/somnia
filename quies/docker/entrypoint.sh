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
    chown -R somnia:somnia /home/somnia

    echo "  Dropping to somnia user..."
    exec gosu somnia "$0" "$@"
fi

# Everything below runs as somnia user

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
