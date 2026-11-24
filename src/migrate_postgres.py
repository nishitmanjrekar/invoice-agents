
"""PostgreSQL migration runner.

Usage:
    python -m src.migrate_postgres status
    python -m src.migrate_postgres upgrade
    python -m src.migrate_postgres verify

SQLite migrations and their runner remain completely separate.
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

from sqlalchemy import inspect, text

from src.db_core import get_postgres_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "src" / "postgres_migrations"

VERSION_TABLE = "postgres_schema_version"
MIGRATION_PATTERN = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def discover_migrations():
    """Discover and validate numbered PostgreSQL migration files."""

    if not MIGRATIONS_DIR.is_dir():
        raise FileNotFoundError(
            f"PostgreSQL migrations directory not found: {MIGRATIONS_DIR}"
        )

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    migrations = []

    for path in files:
        match = MIGRATION_PATTERN.fullmatch(path.name)

        if not match:
            raise ValueError(
                f"Invalid PostgreSQL migration filename: {path.name}"
            )

        version = int(match.group(1))
        content = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

        migrations.append(
            {
                "version": version,
                "filename": path.name,
                "path": path,
                "content": content,
                "checksum": checksum,
            }
        )

    versions = [item["version"] for item in migrations]

    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(
            "PostgreSQL migration versions must be sequential, "
            "starting at 001."
        )

    return migrations


def ensure_version_table(engine):
    """Create the PostgreSQL migration history table if necessary."""

    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"""
            CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (
                version INTEGER PRIMARY KEY,
                filename TEXT NOT NULL UNIQUE,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def get_applied_migrations(engine):
    """Read migration history without modifying the database."""

    with engine.connect() as connection:
        if not inspect(connection).has_table(VERSION_TABLE):
            return []

        result = connection.execute(
            text(
                f"""
                SELECT version, filename, checksum
                FROM {VERSION_TABLE}
                ORDER BY version
                """
            )
        )

        return [dict(row) for row in result.mappings()]


def validate_history(migrations, applied):
    """Check recorded migrations against available migration files."""

    if len(applied) > len(migrations):
        raise ValueError(
            "Database contains migration versions not present in this project."
        )

    for index, record in enumerate(applied):
        migration = migrations[index]

        if record["version"] != migration["version"]:
            raise ValueError("Applied migration history is not sequential.")

        if record["filename"] != migration["filename"]:
            raise ValueError(
                f"Migration filename mismatch for version "
                f"{migration['version']:03d}."
            )

        if record["checksum"] != migration["checksum"]:
            raise ValueError(
                f"Checksum mismatch for {migration['filename']}. "
                "An already-applied migration file was modified."
            )

    return migrations[len(applied):]


def execute_migration(connection, migration):
    """Execute the statements in one migration file.

    Migration files must contain ordinary SQL statements separated
    by semicolons. Do not use procedural blocks containing internal
    semicolons in these migration files.
    """

    statements = migration["content"].split(";")

    for statement in statements:
        statement = statement.strip()

        if statement:
            connection.exec_driver_sql(statement)


def show_status(engine, migrations):
    """Display applied and pending PostgreSQL migrations."""

    applied = get_applied_migrations(engine)
    pending = validate_history(migrations, applied)

    print("Applied:")
    if applied:
        for item in applied:
            print(f"  {item['version']:03d} - {item['filename']}")
    else:
        print("  none")

    print("Pending:")
    if pending:
        for item in pending:
            print(f"  {item['version']:03d} - {item['filename']}")
    else:
        print("  none")


def upgrade(engine, migrations):
    """Apply all pending migrations, one transaction per migration."""

    ensure_version_table(engine)

    applied = get_applied_migrations(engine)
    pending = validate_history(migrations, applied)

    if not pending:
        print("Database is already up to date.")
        return

    for migration in pending:
        with engine.begin() as connection:
            execute_migration(connection, migration)

            connection.execute(
                text(
                    f"""
                    INSERT INTO {VERSION_TABLE}
                        (version, filename, checksum)
                    VALUES
                        (:version, :filename, :checksum)
                    """
                ),
                {
                    "version": migration["version"],
                    "filename": migration["filename"],
                    "checksum": migration["checksum"],
                },
            )

        print(f"Applied: {migration['filename']}")

    print("PostgreSQL migrations completed.")


def verify(engine, migrations):
    """Verify recorded migration history and file checksums."""

    applied = get_applied_migrations(engine)
    pending = validate_history(migrations, applied)

    if not applied:
        print("No PostgreSQL migrations have been applied.")
        return

    print("Applied migration history and checksums are valid.")

    if pending:
        print("Pending migrations exist:")
        for item in pending:
            print(f"  {item['version']:03d} - {item['filename']}")
    else:
        print("All discovered migrations have been applied.")

    print(
        "Note: This verifies migration history, not the physical "
        "database schema."
    )


def main():
    parser = argparse.ArgumentParser(
        description="PostgreSQL migration management"
    )
    parser.add_argument(
        "command",
        choices=("status", "upgrade", "verify"),
    )
    args = parser.parse_args()

    migrations = discover_migrations()
    engine = get_postgres_engine()

    try:
        if args.command == "status":
            show_status(engine, migrations)
        elif args.command == "upgrade":
            upgrade(engine, migrations)
        elif args.command == "verify":
            verify(engine, migrations)
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"PostgreSQL migration error: {exc}", file=sys.stderr)
        sys.exit(1)
