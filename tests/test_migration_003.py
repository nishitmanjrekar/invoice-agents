import shutil
import sqlite3

import pytest

from src import db_core, migrate

NOW = "2026-11-18T00:00:00+00:00"


def _folder(tmp_path, upto, name="partial"):
    folder = tmp_path / name
    folder.mkdir()
    for path in sorted(migrate.MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name[:3]) <= upto:
            shutil.copyfile(path, folder / path.name)
    return folder


def _db_at_002(tmp_path):
    path = tmp_path / "m.db"
    migrate.upgrade(path, create=True, migrations_dir=_folder(tmp_path, 2))
    return path


def _add_invoice(conn, number, thread_id=None, with_thread=True):
    conn.execute(
        "INSERT OR IGNORE INTO vendors (id, vendor_name, is_approved, created_at) "
        "VALUES (1, 'V', 1, ?)",
        (NOW,),
    )
    columns = "vendor_id, invoice_number, invoice_date, currency, total, extracted_data, created_at"
    values = [1, number, "2026-01-01", "INR", 10.0, "{}", NOW]
    if with_thread:
        columns += ", thread_id"
        values.append(thread_id)
    marks = ",".join("?" for _ in values)
    conn.execute(f"INSERT INTO invoices ({columns}) VALUES ({marks})", values)


def _columns(conn):
    return [r["name"] for r in conn.execute("PRAGMA table_info(invoices)")]


def test_003_is_recorded(tmp_path):
    path = tmp_path / "m.db"
    migrate.upgrade(path, create=True)
    conn = sqlite3.connect(path)
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_version")]
    conn.close()
    assert 3 in versions


def test_003_adds_only_thread_id_and_keeps_existing_rows(tmp_path):
    path = _db_at_002(tmp_path)
    conn = db_core.connect(path)
    _add_invoice(conn, "INV-OLD", with_thread=False)
    conn.execute(
        "INSERT INTO audit_logs (invoice_id, action, actor, details, created_at) "
        "VALUES (1, 'INVOICE_RECEIVED', 'SYSTEM', 'x', ?)",
        (NOW,),
    )
    conn.commit()
    before_columns = _columns(conn)
    before_row = dict(conn.execute("SELECT * FROM invoices").fetchone())
    conn.close()

    assert 3 in migrate.upgrade(path)

    conn = db_core.connect(path)
    after_columns = _columns(conn)
    after_row = dict(conn.execute("SELECT * FROM invoices").fetchone())
    audit_count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    conn.close()

    assert after_columns[: len(before_columns)] == before_columns
    assert "thread_id" in after_columns
    assert after_row.pop("thread_id") is None
    assert after_row == before_row
    assert audit_count == 1


def test_many_invoices_may_have_no_thread_id(tmp_db):
    conn = db_core.connect()
    try:
        _add_invoice(conn, "A")
        _add_invoice(conn, "B")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 2
    finally:
        conn.close()


def test_a_thread_id_can_belong_to_only_one_invoice(tmp_db):
    conn = db_core.connect()
    try:
        _add_invoice(conn, "A", "t1")
        with pytest.raises(sqlite3.IntegrityError):
            _add_invoice(conn, "B", "t1")
    finally:
        conn.close()


def test_003_leaves_the_database_consistent(tmp_path):
    path = _db_at_002(tmp_path)
    migrate.upgrade(path)
    conn = db_core.connect(path)
    try:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    assert migrate.verify(path) == []