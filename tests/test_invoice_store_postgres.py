
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import IntegrityError

from src import invoice_store_postgres as store


def _result(row=None, scalar=None):
    result = Mock()
    result.mappings.return_value.first.return_value = row
    result.scalar_one_or_none.return_value = scalar
    return result


def _engine_with_connection(connection):
    @contextmanager
    def transaction():
        yield connection

    return SimpleNamespace(begin=transaction)


def test_has_thread_column(monkeypatch):
    connection = Mock()
    connection.close = Mock()

    monkeypatch.setattr(
        store,
        "get_postgres_engine",
        lambda: SimpleNamespace(connect=lambda: connection),
    )
    monkeypatch.setattr(
        store,
        "inspect",
        lambda conn: SimpleNamespace(
            get_columns=lambda table: [
                {"name": "id"},
                {"name": "thread_id"},
            ]
        ),
    )

    assert store.has_thread_column() is True
    connection.close.assert_called_once()


def test_find_by_thread_returns_invoice(monkeypatch):
    row = {"id": 12, "thread_id": "thread-1"}
    connection = Mock()
    connection.execute.return_value = _result(row=row)

    monkeypatch.setattr(
        store,
        "get_postgres_engine",
        lambda: SimpleNamespace(connect=lambda: connection),
    )

    assert store.find_by_thread("thread-1") == row
    connection.close.assert_called_once()


def test_replay_returns_existing_invoice_without_writes(
    monkeypatch,
):
    connection = Mock()
    connection.execute.return_value = _result(
        row={"id": 12}
    )

    monkeypatch.setattr(
        store,
        "get_postgres_engine",
        lambda: _engine_with_connection(connection),
    )

    invoice_id, created = store.save_invoice_once(
        object(), "pending_approval", "thread-1"
    )

    assert (invoice_id, created) == (12, False)
    connection.execute.assert_called_once()


def test_first_save_inserts_invoice_and_audit(
    monkeypatch,
    make_invoice,
):
    connection = Mock()
    connection.execute.side_effect = [
        _result(),  # No existing invoice for this thread.
        _result(row={"id": 5, "is_approved": True}),
        _result(scalar=42),  # Inserted invoice ID.
        _result(),  # Audit insert.
    ]

    monkeypatch.setattr(
        store,
        "get_postgres_engine",
        lambda: _engine_with_connection(connection),
    )

    invoice_id, created = store.save_invoice_once(
        make_invoice(),
        "pending_approval",
        "thread-1",
    )

    assert (invoice_id, created) == (42, True)
    assert connection.execute.call_count == 4

    audit_call = connection.execute.call_args_list[3]
    assert audit_call.args[1]["invoice_id"] == 42
    assert audit_call.args[1]["action"] == "INVOICE_RECEIVED"


def test_unapproved_vendor_is_rejected(
    monkeypatch,
    make_invoice,
):
    connection = Mock()
    connection.execute.side_effect = [
        _result(),  # No existing invoice.
        _result(),  # Vendor not found.
    ]

    monkeypatch.setattr(
        store,
        "get_postgres_engine",
        lambda: _engine_with_connection(connection),
    )

    with pytest.raises(ValueError, match="Unapproved vendor"):
        store.save_invoice_once(
            make_invoice(),
            "pending_approval",
            "thread-1",
        )

    assert connection.execute.call_count == 2


def test_duplicate_invoice_is_converted_to_value_error(
    monkeypatch,
    make_invoice,
):
    connection = Mock()
    connection.execute.side_effect = [
        _result(),  # No existing invoice for this thread.
        _result(row={"id": 5, "is_approved": True}),
        IntegrityError("INSERT", {}, Exception("unique violation")),
    ]

    monkeypatch.setattr(
        store,
        "get_postgres_engine",
        lambda: _engine_with_connection(connection),
    )

    with pytest.raises(ValueError, match="Duplicate invoice"):
        store.save_invoice_once(
            make_invoice(),
            "pending_approval",
            "thread-2",
        )

    assert connection.execute.call_count == 3
