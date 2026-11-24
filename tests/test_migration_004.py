
import sqlite3

import pytest

from src import db_core, migrate

NOW = "2026-11-19T00:00:00+00:00"


def test_004_is_recorded(tmp_path):
    path = tmp_path / "m.db"

    migrate.upgrade(path, create=True)

    conn = sqlite3.connect(path)
    try:
        versions = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_version ORDER BY version"
            )
        ]
    finally:
        conn.close()

    assert 4 in versions


def test_004_creates_llm_calls_with_expected_columns(tmp_db):
    conn = db_core.connect()
    try:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(llm_calls)")
        }

        assert columns == {
            "id",
            "thread_id",
            "step",
            "provider",
            "model",
            "attempt",
            "outcome",
            "error_type",
            "error_category",
            "prompt_tokens",
            "output_tokens",
            "thinking_tokens",
            "total_tokens",
            "latency_ms",
            "est_cost_usd",
            "pricing_version",
            "started_at",
            "created_at",
        }
    finally:
        conn.close()


def test_004_allows_nullable_usage_and_thread_fields(tmp_db):
    conn = db_core.connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO llm_calls (
                step, provider, model, outcome, started_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("extract", "google-gemini", "gemini-3.5-flash-lite",
             "success", NOW, NOW),
        )
        row = conn.execute(
            "SELECT * FROM llm_calls WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()

        assert row["thread_id"] is None
        assert row["prompt_tokens"] is None
        assert row["output_tokens"] is None
        assert row["thinking_tokens"] is None
        assert row["total_tokens"] is None
        assert row["est_cost_usd"] is None
    finally:
        conn.close()


def test_004_rejects_invalid_outcome(tmp_db):
    conn = db_core.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO llm_calls (
                    step, provider, model, outcome, started_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("extract", "google-gemini", "test-model",
                 "unknown", NOW, NOW),
            )
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("attempt", 0),
        ("prompt_tokens", -1),
        ("output_tokens", -1),
        ("thinking_tokens", -1),
        ("total_tokens", -1),
        ("latency_ms", -1),
        ("est_cost_usd", -0.01),
    ],
)
def test_004_rejects_negative_or_invalid_values(tmp_db, column, value):
    allowed_columns = {
        "attempt",
        "prompt_tokens",
        "output_tokens",
        "thinking_tokens",
        "total_tokens",
        "latency_ms",
        "est_cost_usd",
    }
    assert column in allowed_columns

    conn = db_core.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"""
                INSERT INTO llm_calls (
                    step, provider, model, outcome, started_at, created_at,
                    {column}
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "extract",
                    "google-gemini",
                    "test-model",
                    "success",
                    NOW,
                    NOW,
                    value,
                ),
            )
    finally:
        conn.close()


def test_004_does_not_add_a_thread_id_foreign_key(tmp_db):
    conn = db_core.connect()
    try:
        foreign_keys = conn.execute(
            "PRAGMA foreign_key_list(llm_calls)"
        ).fetchall()

        assert foreign_keys == []
    finally:
        conn.close()


def test_004_preserves_existing_invoice_data(tmp_path):
    path = tmp_path / "m.db"

    migrate.upgrade(
        path,
        create=True,
        migrations_dir=migrate.MIGRATIONS_DIR,
    )

    conn = db_core.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO vendors (
                id, vendor_name, is_approved, created_at
            )
            VALUES (1, 'Existing Vendor', 1, ?)
            """,
            (NOW,),
        )
        conn.execute(
            """
            INSERT INTO invoices (
                vendor_id, invoice_number, invoice_date, currency,
                total, extracted_data, created_at
            )
            VALUES (1, 'INV-OLD', '2026-01-01', 'INR', 10, '{}', ?)
            """,
            (NOW,),
        )
        conn.commit()

        before = dict(
            conn.execute(
                "SELECT * FROM invoices WHERE invoice_number = 'INV-OLD'"
            ).fetchone()
        )

        assert 4 not in migrate.upgrade(path)

        after = dict(
            conn.execute(
                "SELECT * FROM invoices WHERE invoice_number = 'INV-OLD'"
            ).fetchone()
        )

        assert after == before
    finally:
        conn.close()
