import shutil
import sqlite3

import pytest

from src import db_core, migrate

NOW = "2026-11-18T00:00:00+00:00"
ALL = [m["version"] for m in migrate.discover()]


def _folder(tmp_path, upto, name="partial"):
    folder = tmp_path / name
    folder.mkdir()
    for path in sorted(migrate.MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name[:3]) <= upto:
            shutil.copyfile(path, folder / path.name)
    return folder


def _insert(conn, thread_id, sha="a" * 64, status="queued", resub=None):
    conn.execute(
        "INSERT INTO submissions (thread_id, sha256, filename, status, resubmission_of, "
        "created_at, updated_at) VALUES (?, ?, 'f.txt', ?, ?, ?, ?)",
        (thread_id, sha, status, resub, NOW, NOW),
    )


def test_002_is_applied_and_recorded(tmp_path):
    path = tmp_path / "m.db"
    assert migrate.upgrade(path, create=True) == ALL
    conn = sqlite3.connect(path)
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")]
    conn.close()
    assert 2 in versions


def test_002_upgrades_a_database_already_at_001(tmp_path):
    path = tmp_path / "m.db"
    migrate.upgrade(path, create=True, migrations_dir=_folder(tmp_path, 1, "only001"))
    assert migrate.status(path)["pending"] == ALL[1:]
    assert migrate.upgrade(path) == ALL[1:]
    assert migrate.verify(path) == []


def test_002_does_not_change_existing_tables_or_rows(tmp_path):
    path = tmp_path / "m.db"
    migrate.upgrade(path, create=True, migrations_dir=_folder(tmp_path, 1, "only001"))
    conn = db_core.connect(path)
    conn.execute("INSERT INTO vendors (vendor_name, is_approved, created_at) VALUES ('V', 1, ?)", (NOW,))
    conn.commit()
    before = {k: v for k, v in migrate.schema_fingerprint(conn).items() if k != "submissions"}
    conn.close()

    migrate.upgrade(path, migrations_dir=_folder(tmp_path, 2, "upto002"))

    conn = db_core.connect(path)
    after = {k: v for k, v in migrate.schema_fingerprint(conn).items() if k != "submissions"}
    vendors = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    conn.close()
    assert before == after
    assert vendors == 1


def test_thread_id_is_unique(tmp_db):
    conn = db_core.connect()
    try:
        _insert(conn, "t1")
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "t1")
    finally:
        conn.close()


def test_same_hash_allowed_for_different_threads(tmp_db):
    conn = db_core.connect()
    try:
        _insert(conn, "t1")
        _insert(conn, "t2")
        assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 2
    finally:
        conn.close()


def test_invalid_status_is_rejected(tmp_db):
    conn = db_core.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "t1", status="bogus")
    finally:
        conn.close()


def test_resubmission_must_reference_an_existing_submission(tmp_db):
    conn = db_core.connect()
    try:
        _insert(conn, "t1")
        original = conn.execute("SELECT id FROM submissions WHERE thread_id='t1'").fetchone()[0]
        _insert(conn, "t2", resub=original)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "t3", resub=9999)
    finally:
        conn.close()