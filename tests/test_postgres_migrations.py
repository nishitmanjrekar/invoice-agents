
import hashlib

import pytest

from src import migrate_postgres


def write_migration(directory, filename, content):
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


def test_discover_migrations_returns_version_and_checksum(
    tmp_path, monkeypatch
):
    content = "CREATE TABLE example (id INTEGER);"
    write_migration(tmp_path, "001_baseline.sql", content)

    monkeypatch.setattr(
        migrate_postgres,
        "MIGRATIONS_DIR",
        tmp_path,
    )

    migrations = migrate_postgres.discover_migrations()

    assert len(migrations) == 1
    assert migrations[0]["version"] == 1
    assert migrations[0]["filename"] == "001_baseline.sql"
    assert migrations[0]["content"] == content
    assert migrations[0]["checksum"] == hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def test_discover_migrations_rejects_invalid_filename(
    tmp_path, monkeypatch
):
    write_migration(tmp_path, "baseline.sql", "SELECT 1;")

    monkeypatch.setattr(
        migrate_postgres,
        "MIGRATIONS_DIR",
        tmp_path,
    )

    with pytest.raises(ValueError, match="Invalid PostgreSQL migration"):
        migrate_postgres.discover_migrations()


def test_discover_migrations_rejects_missing_version(
    tmp_path, monkeypatch
):
    write_migration(tmp_path, "001_baseline.sql", "SELECT 1;")
    write_migration(tmp_path, "003_users.sql", "SELECT 2;")

    monkeypatch.setattr(
        migrate_postgres,
        "MIGRATIONS_DIR",
        tmp_path,
    )

    with pytest.raises(ValueError, match="sequential"):
        migrate_postgres.discover_migrations()


def test_validate_history_accepts_matching_checksum():
    content = "CREATE TABLE example (id INTEGER);"
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

    migrations = [
        {
            "version": 1,
            "filename": "001_baseline.sql",
            "checksum": checksum,
        }
    ]

    applied = [
        {
            "version": 1,
            "filename": "001_baseline.sql",
            "checksum": checksum,
        }
    ]

    assert migrate_postgres.validate_history(
        migrations, applied
    ) == []


def test_validate_history_rejects_modified_migration():
    migrations = [
        {
            "version": 1,
            "filename": "001_baseline.sql",
            "checksum": "new-checksum",
        }
    ]

    applied = [
        {
            "version": 1,
            "filename": "001_baseline.sql",
            "checksum": "old-checksum",
        }
    ]

    with pytest.raises(ValueError, match="Checksum mismatch"):
        migrate_postgres.validate_history(migrations, applied)


def test_execute_migration_executes_statements_in_order():
    class FakeConnection:
        def __init__(self):
            self.statements = []

        def exec_driver_sql(self, statement):
            self.statements.append(statement)

    connection = FakeConnection()

    migration = {
        "content": """
        CREATE TABLE first_table (id INTEGER);

        CREATE TABLE second_table (id INTEGER);
        """
    }

    migrate_postgres.execute_migration(connection, migration)

    assert connection.statements == [
        "CREATE TABLE first_table (id INTEGER)",
        "CREATE TABLE second_table (id INTEGER)",
    ]
