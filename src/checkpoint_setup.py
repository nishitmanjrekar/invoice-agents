
"""Explicit initialization for LangGraph PostgreSQL checkpoint storage.

Run with:
    python -m src.checkpoint_setup

This initializes LangGraph-owned checkpoint tables and migrations only.
It does not run application database migrations.
"""

import os

from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy.engine import make_url


def setup_postgres_checkpointer(database_url=None):
    """Initialize PostgreSQL checkpoint tables and migrations."""

    configured_url = database_url or os.getenv("DATABASE_URL")

    if not configured_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. "
            "Set it before initializing PostgreSQL checkpoints."
        )

    url = make_url(configured_url)

    if url.drivername not in (
        "postgres",
        "postgresql",
        "postgresql+psycopg",
    ):
        raise ValueError(
            "DATABASE_URL must use PostgreSQL with the psycopg driver."
        )

    connection_url = url.set(
        drivername="postgresql"
    ).render_as_string(hide_password=False)

    with PostgresSaver.from_conn_string(connection_url) as saver:
        saver.setup()


def main():
    setup_postgres_checkpointer()
    print("PostgreSQL checkpoint storage initialized successfully.")


if __name__ == "__main__":
    main()
