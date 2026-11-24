"""Numbered SQL migrations for app.db.

    python -m src.migrate status  [--db PATH]
    python -m src.migrate upgrade [--db PATH] [--create]
    python -m src.migrate verify  [--db PATH]

Rules for src/migrations/NNN_name.sql:
  * numbering is contiguous from 001, and an applied file is never edited
  * plain SQL ending in ';' with no BEGIN/COMMIT (each file runs in one transaction)
  * the runner stores each file's SHA-256 and refuses to run if it later changes
"""
import argparse
import hashlib
import re
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from src import db_core

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_NAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")

SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


class MigrationError(Exception):
    pass


def discover(migrations_dir=None):
    directory = Path(migrations_dir) if migrations_dir else MIGRATIONS_DIR
    found = []

    for path in sorted(directory.glob("*.sql")):
        match = _NAME.match(path.name)
        if not match:
            raise MigrationError(f"Bad migration file name: {path.name} (expected NNN_name.sql)")

        sql = path.read_text(encoding="utf-8")

        if not sql.rstrip().endswith(";"):
            raise MigrationError(f"{path.name} must end with ';'")

        found.append({
            "version": int(match.group(1)),
            "name": match.group(2),
            "sql": sql,
            "checksum": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        })

    versions = [m["version"] for m in found]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"Migration numbers must be contiguous from 001, got {versions}")

    return found


def _open(db_path, create):
    path = Path(db_path) if db_path is not None else db_core.DB_PATH
    connection = db_core.connect(path, require_exists=not create)
    connection.isolation_level = None  # autocommit; each migration manages its own transaction
    return connection


def _recorded(connection):
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if not exists:
        return {}
    return {
        row["version"]: row["checksum"]
        for row in connection.execute("SELECT version, checksum FROM schema_version")
    }


def upgrade(db_path=None, *, create=False, migrations_dir=None):
    """Apply pending migrations. Returns the list of versions applied now."""

    migrations = discover(migrations_dir)
    known = {m["version"]: m for m in migrations}
    connection = _open(db_path, create)
    applied_now = []

    try:
        connection.execute(SCHEMA_VERSION_DDL)
        recorded = _recorded(connection)

        for version, checksum in recorded.items():
            if version not in known:
                raise MigrationError(
                    f"Database has migration {version:03d} that this code does not know. "
                    "Use newer code."
                )
            if known[version]["checksum"] != checksum:
                raise MigrationError(
                    f"Migration {version:03d}_{known[version]['name']} was edited after it "
                    "was applied. Add a new migration instead."
                )

        for migration in migrations:
            if migration["version"] in recorded:
                continue

            stamp = datetime.now(UTC).isoformat()
            script = (
                "BEGIN IMMEDIATE;\n"
                + migration["sql"]
                + "\nINSERT INTO schema_version (version, name, checksum, applied_at) VALUES "
                f"({migration['version']}, '{migration['name']}', "
                f"'{migration['checksum']}', '{stamp}');\nCOMMIT;"
            )

            try:
                connection.executescript(script)
            except sqlite3.Error as error:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise MigrationError(
                    f"Migration {migration['version']:03d}_{migration['name']} failed "
                    f"and was rolled back: {error}"
                ) from error

            applied_now.append(migration["version"])
    finally:
        connection.close()

    return applied_now


def status(db_path=None, migrations_dir=None):
    migrations = discover(migrations_dir)
    connection = _open(db_path, create=False)
    try:
        recorded = _recorded(connection)
    finally:
        connection.close()
    return {
        "applied": sorted(recorded),
        "pending": [m["version"] for m in migrations if m["version"] not in recorded],
    }


def _normalise_sql(sql):
    return re.sub(r"\s+", "", sql or "").lower()


def schema_fingerprint(connection):
    """Comparable description of tables, columns, keys, indexes and DDL text."""

    fingerprint = {}
    tables = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name != 'schema_version' ORDER BY name"
    ).fetchall()

    for row in tables:
        name, sql = row[0], row[1]
        columns = [tuple(r)[1:6] for r in connection.execute(f'PRAGMA table_info("{name}")')]
        foreign_keys = sorted(
            (tuple(r)[2:7] for r in connection.execute(f'PRAGMA foreign_key_list("{name}")')),
            key=repr,
        )
        indexes = []
        for idx in connection.execute(f'PRAGMA index_list("{name}")'):
            idx_name, unique, origin = idx[1], idx[2], idx[3]
            cols = tuple(c[2] for c in connection.execute(f'PRAGMA index_info("{idx_name}")'))
            indexes.append((idx_name if origin == "c" else "auto", unique, cols))
        fingerprint[name] = {
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": sorted(indexes, key=repr),
            "sql": _normalise_sql(sql),
        }

    fingerprint["__triggers_and_views__"] = sorted(
        tuple(r) for r in connection.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view')"
        )
    )
    return fingerprint


def compare(expected, actual):
    differences = []
    for name in sorted(set(expected) | set(actual)):
        if name not in actual:
            differences.append(f"missing: {name}")
        elif name not in expected:
            differences.append(f"unexpected: {name}")
        elif expected[name] != actual[name]:
            differences.append(f"different: {name}")
    return differences


def verify(db_path=None, migrations_dir=None):
    """Compare a database's schema with what the migrations would build."""

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        reference_path = Path(tmp) / "reference.db"
        upgrade(reference_path, create=True, migrations_dir=migrations_dir)
        reference_conn = _open(reference_path, create=False)
        target_conn = _open(db_path, create=False)
        try:
            return compare(schema_fingerprint(reference_conn), schema_fingerprint(target_conn))
        finally:
            reference_conn.close()
            target_conn.close()


def main():
    parser = argparse.ArgumentParser(prog="python -m src.migrate")
    parser.add_argument("command", choices=["status", "upgrade", "verify"])
    parser.add_argument("--db", default=None)
    parser.add_argument("--create", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "upgrade":
            applied = upgrade(args.db, create=args.create)
            print("Applied:", [f"{v:03d}" for v in applied] or "nothing (already up to date)")
        elif args.command == "status":
            info = status(args.db)
            print("Applied:", [f"{v:03d}" for v in info["applied"]] or "none (not stamped)")
            print("Pending:", [f"{v:03d}" for v in info["pending"]] or "none")
        else:
            differences = verify(args.db)
            if differences:
                print("Schema DIFFERS from migrations:")
                for line in differences:
                    print("  -", line)
                sys.exit(1)
            print("Schema matches the migrations exactly.")
    except (MigrationError, FileNotFoundError) as error:
        print(f"ERROR: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()