import sqlite3

import pytest

from src import db_core


def test_connect_applies_required_pragmas(tmp_path):
    conn = db_core.connect(tmp_path / "x.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db_core.BUSY_TIMEOUT_MS
    finally:
        conn.close()


def test_rows_are_accessible_by_name(tmp_path):
    conn = db_core.connect(tmp_path / "x.db")
    try:
        row = conn.execute("SELECT 1 AS answer").fetchone()
        assert row["answer"] == 1
    finally:
        conn.close()


def test_require_exists_raises_for_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        db_core.connect(tmp_path / "missing.db", require_exists=True)


def test_foreign_keys_are_enforced(tmp_db):
    conn = db_core.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO invoices (vendor_id, invoice_number, invoice_date, currency, "
                "total, extracted_data, created_at) VALUES (999, 'X', '2026-01-01', 'INR', "
                "1, '{}', 'now')"
            )
    finally:
        conn.close()


def test_reader_is_not_blocked_by_an_open_writer(tmp_db):
    writer = db_core.connect()
    reader = db_core.connect()
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO vendors (vendor_name, is_approved, created_at) VALUES ('V', 1, 'now')"
        )
        assert reader.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 0
        writer.commit()
        assert reader.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 1
    finally:
        writer.close()
        reader.close()