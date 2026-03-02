#!/bin/bash
# Somnia entrypoint - runs both Flask API and MCP server

set -e

echo "Starting Somnia services..."
echo "  APP_DIR: ${SOMNIA_APP_DIR:-/app}"
echo "  DATA_DIR: ${SOMNIA_DATA_DIR:-/data/somnia}"
echo "  API port: ${SOMNIA_PORT:-8010}"
echo "  MCP port: ${SOMNIA_MCP_PORT:-8011}"

# Ensure data subdirectories exist (volume mount may not have them)
mkdir -p /data/somnia/backups /data/somnia/solo-work /data/somnia/logs 2>/dev/null || true

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
