
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src import review_postgres as review


def _result(row=None, rowcount=None):
    result = Mock()
    result.mappings.return_value.first.return_value = row

    if rowcount is not None:
        result.rowcount = rowcount

    return result


def _mock_engine(connection):
    @contextmanager
    def transaction():
        yield connection

    return SimpleNamespace(begin=transaction)


def test_approve_pending_invoice_updates_status_and_audit(
    monkeypatch,
):
    connection = Mock()
    connection.execute.side_effect = [
        _result(
            {
                "id": 10,
                "invoice_number": "INV-001",
                "status": "pending_approval",
            }
        ),
        _result(rowcount=1),
        _result(),
    ]

    monkeypatch.setattr(
        review,
        "get_postgres_engine",
        lambda: _mock_engine(connection),
    )

    assert review.make_decision(10, "approve", "verified") is True
    assert connection.execute.call_count == 3

    audit_params = connection.execute.call_args_list[2].args[1]
    assert audit_params["action"] == "INVOICE_APPROVED"
    assert audit_params["actor"] == "human_reviewer"


def test_human_review_invoice_cannot_be_approved(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(
        {
            "id": 11,
            "invoice_number": "INV-002",
            "status": "human_review",
        }
    )

    monkeypatch.setattr(
        review,
        "get_postgres_engine",
        lambda: _mock_engine(connection),
    )

    assert review.make_decision(11, "approve", "verified") is False
    assert connection.execute.call_count == 1


def test_reject_review_case_updates_case_and_audit(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(
            {
                "id": 20,
                "invoice_number": "INV-003",
                "case_type": "unknown_vendor",
                "status": "open",
                "existing_invoice_id": None,
            }
        ),
        _result(rowcount=1),
        _result(),
    ]

    monkeypatch.setattr(
        review,
        "get_postgres_engine",
        lambda: _mock_engine(connection),
    )

    assert review.reject_review_case(20, "unverified vendor") is True
    assert connection.execute.call_count == 3

    audit_params = connection.execute.call_args_list[2].args[1]
    assert audit_params["action"] == "INVOICE_REVIEW_CASE_REJECTED"
    assert audit_params["actor"] == "human_reviewer"
    assert audit_params["invoice_id"] is None


def test_rejection_without_reason_does_not_access_database(
    monkeypatch,
):
    def unexpected_database_access():
        pytest.fail("Database should not be accessed")

    monkeypatch.setattr(
        review,
        "get_postgres_engine",
        unexpected_database_access,
    )

    assert review.make_decision(10, "reject", "  ") is False
    assert review.reject_review_case(20, "") is False


def test_database_error_returns_false(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = SQLAlchemyError(
        "simulated database failure"
    )

    monkeypatch.setattr(
        review,
        "get_postgres_engine",
        lambda: _mock_engine(connection),
    )

    assert review.make_decision(10, "approve") is False

def test_custom_actor_is_recorded_in_audit(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(
            {
                "id": 12,
                "invoice_number": "INV-004",
                "status": "pending_approval",
            }
        ),
        _result(rowcount=1),
        _result(),
    ]

    monkeypatch.setattr(
        review,
        "get_postgres_engine",
        lambda: _mock_engine(connection),
    )

    assert review.make_decision(
        12, "approve", "verified", actor="nishit"
    ) is True

    audit_params = connection.execute.call_args_list[2].args[1]
    assert audit_params["actor"] == "nishit"