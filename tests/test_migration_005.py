import shutil
import sqlite3

import pytest

from src import db_core, migrate

NOW = "2026-11-20T00:00:00+00:00"


def _folder(tmp_path, upto, name="partial"):
    folder = tmp_path / name
    folder.mkdir()
    for path in sorted(migrate.MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name[:3]) <= upto:
            shutil.copyfile(path, folder / path.name)
    return folder


def _insert(conn, **overrides):
    row = {
        "username": "alice", "password_hash": "h", "role": "reviewer",
        "created_at": NOW, "updated_at": NOW,
    }
    row.update(overrides)
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO users ({columns}) VALUES ({marks})", list(row.values()))


def test_005_is_recorded(tmp_path):
    path = tmp_path / "m.db"
    migrate.upgrade(path, create=True)
    conn = sqlite3.connect(path)
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_version")]
    conn.close()
    assert 5 in versions


def test_005_only_adds_the_users_table_and_keeps_rows(tmp_path):
    upto4 = _folder(tmp_path, 4, "upto004")
    path = tmp_path / "m.db"
    migrate.upgrade(path, create=True, migrations_dir=upto4)
    conn = db_core.connect(path)
    conn.execute(
        "INSERT INTO vendors (vendor_name, is_approved, created_at) VALUES ('V', 1, ?)", (NOW,)
    )
    conn.commit()
    before = migrate.schema_fingerprint(conn)
    conn.close()

    assert migrate.upgrade(path, migrations_dir=_folder(tmp_path, 5, "upto005")) == [5]

    conn = db_core.connect(path)
    after = migrate.schema_fingerprint(conn)
    vendors = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    assert set(after) - set(before) == {"users"}
    assert {k: v for k, v in after.items() if k != "users"} == before
    assert (vendors, fk, integrity) == (1, [], "ok")


def test_defaults_are_applied(tmp_db):
    conn = db_core.connect()
    try:
        _insert(conn)
        row = dict(conn.execute("SELECT * FROM users").fetchone())
    finally:
        conn.close()
    assert (row["active"], row["failed_attempts"]) == (1, 0)
    assert row["locked_until"] is None and row["last_login_at"] is None


def test_usernames_are_unique_ignoring_case(tmp_db):
    conn = db_core.connect()
    try:
        _insert(conn, username="Alice")
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, username="alice")
    finally:
        conn.close()


@pytest.mark.parametrize(
    "overrides",
    [{"role": "superuser"}, {"active": 2}, {"failed_attempts": -1}, {"password_hash": None}],
)
def test_invalid_values_are_rejected(tmp_db, overrides):
    conn = db_core.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, **overrides)
    finally:
        conn.close()


@pytest.mark.parametrize("role", ["admin", "finance_manager", "reviewer", "uploader"])
def test_all_four_roles_are_accepted(tmp_db, role):
    conn = db_core.connect()
    try:
        _insert(conn, role=role)
    finally:
        conn.close()