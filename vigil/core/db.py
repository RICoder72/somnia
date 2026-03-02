"""
Database connection pool and migration runner for Vigil.

Uses asyncpg for PostgreSQL access. Shares the Constellation
database with Store (during migration) and eventually owns
the entity/schema/relationship tables directly.
"""

import asyncpg
import pathlib
from config import DATABASE_URL, POOL_MIN_SIZE, POOL_MAX_SIZE

_pool: asyncpg.Pool | None = None

SCHEMA_DIR = pathlib.Path(__file__).resolve().parent.parent / "schema"


async def get_pool() -> asyncpg.Pool:
    """Get or create the connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
        )
    return _pool


async def init_db():
    """Run pending migrations on startup."""
    global _pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Create migrations tracking table (namespaced to avoid collision with Store's)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _vigil_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # Get already-applied migrations
        applied = {
            row["filename"]
            for row in await conn.fetch("SELECT filename FROM _vigil_migrations")
        }

        # Find and apply pending SQL files in order
        sql_files = sorted(SCHEMA_DIR.glob("*.sql"))
        for sql_file in sql_files:
            if sql_file.name not in applied:
                sql = sql_file.read_text()
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO _vigil_migrations (filename) VALUES ($1)",
                    sql_file.name,
                )
                print(f"Applied migration: {sql_file.name}")

    # Close the pool so it gets recreated on the correct event loop
    await pool.close()
    _pool = None

    print("Database ready.")
