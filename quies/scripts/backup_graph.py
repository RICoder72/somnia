#!/usr/bin/env python3
"""
Somnia graph backup — dumps all tables to JSON, keeps last N backups.

Run nightly via Somnia's dream scheduler or manually.
Backs up to /data/backups/somnia-nightly-YYYYMMDD.json
"""

import asyncio
import json
import os
import glob
from datetime import datetime, date
from pathlib import Path

BACKUP_DIR = Path("/data/somnia/backups")
DATABASE_URL = os.environ.get(
    "SOMNIA_DATABASE_URL",
    "postgresql://constellation:FPCsUawkvlxe6O_lSt0_7AiEAJO8DVr4@constellation-postgres:5432/somnia"
)
KEEP_COUNT = 2
TABLES = [
    "nodes", "edges", "stm_nodes", "stm_edges", "inbox",
    "dream_log", "diagnostics", "contexts", "context_nodes", "activity",
]


async def dump_graph():
    """Dump all Somnia tables to a JSON file."""
    import asyncpg

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    dest = BACKUP_DIR / f"somnia-nightly-{stamp}.json"

    # Skip if today's backup already exists
    if dest.exists():
        print(f"Backup already exists: {dest}")
        return str(dest)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        backup = {"timestamp": datetime.now().isoformat(), "tables": {}}
        total_rows = 0

        for table in TABLES:
            rows = await conn.fetch(f"SELECT * FROM {table}")
            records = []
            for row in rows:
                record = dict(row)
                for k, v in record.items():
                    if isinstance(v, (datetime, date)):
                        record[k] = v.isoformat()
                    elif isinstance(v, bytes):
                        record[k] = v.hex()
                records.append(record)
            backup["tables"][table] = records
            total_rows += len(records)

        with open(dest, "w") as f:
            json.dump(backup, f, indent=2, default=str)

        size = dest.stat().st_size
        print(f"Backup complete: {dest} ({size:,} bytes, {total_rows} rows)")
    finally:
        await conn.close()

    # Rotate — keep only the last N
    backups = sorted(glob.glob(str(BACKUP_DIR / "somnia-nightly-*.json")))
    while len(backups) > KEEP_COUNT:
        old = backups.pop(0)
        os.remove(old)
        print(f"Rotated out: {old}")

    return str(dest)


if __name__ == "__main__":
    result = asyncio.run(dump_graph())
