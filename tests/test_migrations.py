import shutil
import sqlite3

import pytest

from src import db, db_core, migrate

TABLES = {"vendors", "purchase_orders", "invoices", "audit_logs", "invoice_review_cases"}
ALL = [m["version"] for m in migrate.discover()]


def _folder(tmp_path, upto, name="partial"):
    """A migrations folder containing only versions up to `upto`."""
    folder = tmp_path / name
    folder.mkdir()
    for path in sorted(migrate.MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name[:3]) <= upto:
            shutil.copyfile(path, folder / path.name)
    return folder


def _fingerprint(path):
    conn = db_core.connect(path)
    try:
        return migrate.schema_fingerprint(conn)
    finally:
        conn.close()


def _without(fingerprint, *names):
    return {k: v for k, v in fingerprint.items() if k not in names}


def test_migrations_create_all_tables_and_record_every_version(tmp_path):
    path = tmp_path / "m.db"
    assert migrate.upgrade(path, create=True) == ALL
    conn = sqlite3.connect(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")]
    conn.close()
    assert TABLES <= names
    assert versions == ALL


def test_upgrade_is_idempotent(tmp_path):
    path = tmp_path / "m.db"
    migrate.upgrade(path, create=True)
    assert migrate.upgrade(path) == []


def test_baseline_migration_matches_legacy_initialize_db(tmp_path, monkeypatch):
    migrated = tmp_path / "migrated.db"
    migrate.upgrade(migrated, create=True, migrations_dir=_folder(tmp_path, 1))

    legacy = tmp_path / "legacy.db"
    monkeypatch.setattr(db_core, "DB_PATH", legacy)
    db.initialize_db()

    assert _fingerprint(migrated) == _fingerprint(legacy)


def test_upgrade_preserves_existing_data_and_only_adds(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    monkeypatch.setattr(db_core, "DB_PATH", legacy)
    db.initialize_db()
    db.seed_vendors()

    before = _fingerprint(legacy)
    assert migrate.status(legacy)["applied"] == []

    assert migrate.upgrade(legacy) == ALL

    after = _fingerprint(legacy)
    assert _without(
    after, "submissions", "invoices", "llm_calls", "schema_version", "users"
    ) == _without(before, "invoices")
    old_columns = [c[0] for c in before["invoices"]["columns"]]
    new_columns = [c[0] for c in after["invoices"]["columns"]]
    assert new_columns[: len(old_columns)] == old_columns
    conn = sqlite3.connect(legacy)
    vendor_count = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    conn.close()
    assert vendor_count == len(db.APPROVED_VENDORS)
    assert migrate.verify(legacy) == []


def test_edited_migration_is_detected(tmp_path):
    path = tmp_path / "m.db"
    migrate.upgrade(path, create=True)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_version SET checksum = 'tampered'")
    conn.commit()
    conn.close()
    with pytest.raises(migrate.MigrationError):
        migrate.upgrade(path)


def test_failed_migration_rolls_back_completely(tmp_path):
    folder = tmp_path / "m"
    folder.mkdir()
    (folder / "001_first.sql").write_text("CREATE TABLE a (id INTEGER);")
    (folder / "002_bad.sql").write_text(
        "CREATE TABLE b (id INTEGER);\nINSERT INTO missing_table VALUES (1);"
    )
    path = tmp_path / "t.db"

    with pytest.raises(migrate.MigrationError):
        migrate.upgrade(path, create=True, migrations_dir=folder)

    conn = sqlite3.connect(path)
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_version")]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert versions == [1]
    assert "a" in tables
    assert "b" not in tables


def test_numbering_gap_is_rejected(tmp_path):
    folder = tmp_path / "m"
    folder.mkdir()
    (folder / "001_a.sql").write_text("CREATE TABLE a (id INTEGER);")
    (folder / "003_c.sql").write_text("CREATE TABLE c (id INTEGER);")
    with pytest.raises(migrate.MigrationError):
        migrate.discover(folder)


def test_upgrade_refuses_to_create_a_missing_database_by_default(tmp_path):
    with pytest.raises(FileNotFoundError):
        migrate.upgrade(tmp_path / "typo.db")