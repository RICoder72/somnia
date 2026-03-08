#!/usr/bin/env python3
"""
Somnia nightly DB backup — called by cron at 02:00 daily.
Dumps somnia-postgres to /data/backups/db/ and prunes old dumps.
"""

import subprocess
import sys
from pathlib import Path
from datetime import datetime

BACKUP_DIR = Path("/data/backups/db")
CONTAINER  = "somnia-postgres"
DB_USER    = "somnia"
DB_NAME    = "somnia"
RETAIN_DAYS = 14

def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def main():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dump_file = BACKUP_DIR / f"somnia_{ts}.dump"

    log("=== DB backup starting ===")

    # Get postgres password from running container env
    try:
        pw_result = subprocess.run(
            ["docker", "exec", CONTAINER, "sh", "-c", "echo $POSTGRES_PASSWORD"],
            capture_output=True, text=True, timeout=10
        )
        pgpass = pw_result.stdout.strip() or "FPCsUawkvlxe6O_lSt0_7AiEAJO8DVr4"
    except Exception:
        pgpass = "FPCsUawkvlxe6O_lSt0_7AiEAJO8DVr4"

    # pg_dump
    try:
        result = subprocess.run(
            ["docker", "exec", "-e", f"PGPASSWORD={pgpass}", CONTAINER,
             "pg_dump", "-U", DB_USER, "-d", DB_NAME, "-Fc"],
            capture_output=True, timeout=120
        )
        if result.returncode != 0:
            log(f"  FAILED: {result.stderr.decode()[:300]}")
            sys.exit(1)
        dump_file.write_bytes(result.stdout)
        mb = dump_file.stat().st_size / 1048576
        log(f"  OK — {dump_file.name} ({mb:.2f} MB)")
    except subprocess.TimeoutExpired:
        log("  FAILED: pg_dump timed out")
        sys.exit(1)
    except Exception as e:
        log(f"  EXCEPTION: {e}")
        sys.exit(1)

    # Prune old dumps
    cutoff = datetime.now().timestamp() - (RETAIN_DAYS * 86400)
    pruned = sum(1 for f in BACKUP_DIR.glob("somnia_*.dump")
                 if f.stat().st_mtime < cutoff and f.unlink() is None)
    remaining = len(list(BACKUP_DIR.glob("somnia_*.dump")))
    log(f"  Pruned {pruned} old dump(s) — {remaining} retained")
    log("=== Backup complete ===")

if __name__ == "__main__":
    main()
