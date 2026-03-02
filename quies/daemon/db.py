"""
Somnia database module — PostgreSQL via psycopg2.

Connection pooling, migration runner, and query helpers.
"""

import os
import psycopg2
import psycopg2.pool
import psycopg2.extras
from pathlib import Path

# Register the RealDictCursor as default for dict-style rows
psycopg2.extras.register_default_jsonb(loads=__import__('json').loads)

DATABASE_URL = os.environ.get("SOMNIA_DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "SOMNIA_DATABASE_URL environment variable is required. "
        "Set it to a PostgreSQL connection string, e.g.: "
        "postgresql://user:pass@host:5432/somnia"
    )

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"

_pool = None


def get_pool():
    """Get or create the connection pool."""
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=DATABASE_URL,
        )
    return _pool


def get_conn():
    """Get a connection from the pool. Caller must call put_conn() when done."""
    pool = get_pool()
    conn = pool.getconn()
    conn.autocommit = False
    return conn


def put_conn(conn):
    """Return a connection to the pool."""
    pool = get_pool()
    pool.putconn(conn)


def execute(query, params=None, fetch=None):
    """
    Execute a query and return results.
    
    fetch: None (no return), 'one', 'all'
    Returns dicts via RealDictCursor.
    """
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            if fetch == 'one':
                result = cur.fetchone()
            elif fetch == 'all':
                result = cur.fetchall()
            else:
                result = None
            conn.commit()
            return result
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def execute_many(query, params_list):
    """Execute a query with multiple parameter sets."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for params in params_list:
                cur.execute(query, params)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def init_db():
    """Run the initial schema and any pending migrations."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Apply base schema
            init_sql = SCHEMA_DIR / "pg_init.sql"
            if init_sql.exists():
                cur.execute(init_sql.read_text())
            conn.commit()

            # Check for and apply numbered migrations (pg_NNN_*.sql)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS _somnia_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.commit()

            cur.execute("SELECT filename FROM _somnia_migrations")
            applied = {row[0] for row in cur.fetchall()}

            migration_files = sorted(SCHEMA_DIR.glob("pg_[0-9]*.sql"))
            for mf in migration_files:
                if mf.name not in applied:
                    cur.execute(mf.read_text())
                    cur.execute(
                        "INSERT INTO _somnia_migrations (filename) VALUES (%s)",
                        (mf.name,)
                    )
                    conn.commit()
                    print(f"Applied migration: {mf.name}")

        print("Somnia database ready.")
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def close_pool():
    """Close all connections in the pool."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
