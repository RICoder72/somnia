#!/bin/bash
# Fabrica entrypoint — starts cron daemon then the MCP server

set -e

# Write the nightly backup crontab
# Dumps somnia-postgres to /data/backups/db/ at 02:00 daily
cat > /etc/cron.d/somnia-backup << 'EOF'
0 2 * * * root /usr/local/bin/python /app/db_backup_cron.py >> /data/backups/db/backup.log 2>&1
EOF
chmod 0644 /etc/cron.d/somnia-backup

# Start cron in the background
cron

echo "[Fabrica] Nightly DB backup cron scheduled — runs at 02:00 daily"

# Start the MCP server
exec python server.py
