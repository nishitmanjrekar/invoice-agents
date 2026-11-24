
"""Shared database connection configuration.

SQLite remains the default for local development and tests.
PostgreSQL is enabled when DATABASE_URL is configured.

The existing SQLite connect() interface is intentionally preserved.
"""

import os
import sqlite3
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "app.db"

BUSY_TIMEOUT_MS = 5000
DATABASE_URL_ENV = "DATABASE_URL"


def get_database_backend():
    """Return the configured application database backend."""

    return "postgresql" if os.getenv(DATABASE_URL_ENV) else "sqlite"


@lru_cache(maxsize=4)
def _create_postgres_engine(database_url: str) -> Engine:
    """Create and cache a PostgreSQL SQLAlchemy engine."""

    url = make_url(database_url)

    if url.drivername not in (
        "postgres",
        "postgresql",
        "postgresql+psycopg",
    ):
        raise ValueError(
            "DATABASE_URL must use PostgreSQL with the psycopg driver."
        )

    url = url.set(drivername="postgresql+psycopg")

    return create_engine(
        url,
        pool_pre_ping=True,
    )


def get_postgres_engine(database_url=None):
    """Return the PostgreSQL engine using DATABASE_URL or an explicit URL."""

    configured_url = database_url or os.getenv(DATABASE_URL_ENV)

    if not configured_url:
        raise ValueError(
            "DATABASE_URL is not configured. "
            "Set it to enable PostgreSQL."
        )

    return _create_postgres_engine(configured_url)

def connect_postgres():
    """Open a PostgreSQL SQLAlchemy Core connection."""

    return get_postgres_engine().connect()

def connect(db_path=None, *, require_exists=False):
    """Open a SQLite connection with the existing required settings."""

    path = Path(db_path) if db_path is not None else DB_PATH

    if require_exists and not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}. Run python -m src.db first."
        )

    connection = sqlite3.connect(
        path,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")

    return connection
