
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src import db, db_core


@pytest.mark.parametrize(
    ("function_name", "args", "postgres_method"),
    [
        ("initialize_db", (), "initialize_db"),
        ("seed_vendors", (), "seed_vendors"),
        ("seed_purchase_orders", (), "seed_purchase_orders"),
        ("get_vendor", ("Acme Supplies",), "get_vendor"),
        (
            "get_purchase_order",
            ("Acme Supplies", "PO-2001"),
            "get_purchase_order",
        ),
        (
            "find_duplicate",
            ("Acme Supplies", "INV-001"),
            "find_duplicate",
        ),
        ("save_invoice", ("fake_invoice", "received"), "save_invoice"),
        (
            "create_review_case",
            (
                "fake_invoice",
                {"checks": {"duplicate": {}}, "risk_level": "low"},
                "unknown_vendor",
            ),
            "create_review_case",
        ),
        (
            "log_audit_event",
            (1, "TEST_EVENT", "SYSTEM", "Test details"),
            "log_audit_event",
        ),
        ("get_database_summary", (), "get_database_summary"),
    ],
)
def test_public_database_function_routes_to_postgres(
    monkeypatch,
    function_name,
    args,
    postgres_method,
):
    expected = object()
    method = Mock(return_value=expected)

    fake_postgres = SimpleNamespace(**{postgres_method: method})

    monkeypatch.setattr(db, "_postgres", lambda: True)
    monkeypatch.setattr(db, "_get_postgres_db", lambda: fake_postgres)

    result = getattr(db, function_name)(*args)

    method.assert_called_once_with(*args)

    if function_name == "seed_vendors":
        assert result is None
    else:
        assert result is expected


def test_sqlite_is_default_when_database_url_is_missing(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert db_core.get_database_backend() == "sqlite"


def test_postgresql_is_selected_when_database_url_is_configured(
    monkeypatch,
):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://test:test@localhost/test_db",
    )

    assert db_core.get_database_backend() == "postgresql"
