
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src import submissions_postgres as submissions


def _result(row=None, rows=None, scalar=None, rowcount=1):
    result = Mock()
    result.mappings.return_value.first.return_value = row
    result.mappings.return_value.all.return_value = rows or []
    result.scalar_one.return_value = scalar
    result.rowcount = rowcount
    return result


def _engine_with_connection(connection):
    @contextmanager
    def transaction():
        yield connection

    return SimpleNamespace(begin=transaction)


def _patch_engine(monkeypatch, connection):
    monkeypatch.setattr(
        submissions,
        "get_postgres_engine",
        lambda: _engine_with_connection(connection),
    )


def test_ledger_available(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(scalar="submissions")
    _patch_engine(monkeypatch, connection)

    assert submissions.ledger_available() is True


def test_ledger_unavailable(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(scalar=None)
    _patch_engine(monkeypatch, connection)

    assert submissions.ledger_available() is False


def test_get_by_id_returns_dict(monkeypatch):
    row = {"id": 10, "thread_id": "thread-1"}
    connection = Mock()
    connection.execute.return_value = _result(row=row)
    _patch_engine(monkeypatch, connection)

    assert submissions.get_by_id(10) == row


def test_get_by_thread_returns_none(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(row=None)
    _patch_engine(monkeypatch, connection)

    assert submissions.get_by_thread("missing") is None


def test_find_by_hash_returns_row(monkeypatch):
    row = {"id": 10, "sha256": "a" * 64, "status": "queued"}
    connection = Mock()
    connection.execute.return_value = _result(row=row)
    _patch_engine(monkeypatch, connection)

    assert submissions.find_by_hash("a" * 64) == row


def test_create_submission_inserts_and_returns_row(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(scalar=42),
        _result(row={"id": 42, "status": "queued"}),
    ]
    _patch_engine(monkeypatch, connection)

    row = submissions.create_submission(
        "thread-1",
        "a" * 64,
        "invoice.txt",
        mime="text/plain",
        submitted_by="tester",
    )

    assert row["id"] == 42
    assert row["status"] == "queued"
    assert connection.execute.call_count == 2

    insert_params = connection.execute.call_args_list[0].args[1]
    assert insert_params["thread_id"] == "thread-1"
    assert insert_params["sha256"] == "a" * 64
    assert insert_params["submitted_by"] == "tester"


def test_register_submission_acquires_advisory_lock(
    monkeypatch,
):
    connection = Mock()
    connection.execute.return_value = _result()
    _patch_engine(monkeypatch, connection)

    existing = {
        "id": 7,
        "sha256": "a" * 64,
        "status": "queued",
    }

    monkeypatch.setattr(
        submissions,
        "find_by_hash",
        lambda sha256, conn=None: existing,
    )

    row, created = submissions.register_submission(
        "thread-2",
        "a" * 64,
        "invoice.txt",
    )

    assert row == existing
    assert created is False
    connection.execute.assert_called_once()

    sql = str(connection.execute.call_args.args[0])
    assert "pg_advisory_xact_lock" in sql


def test_register_submission_requires_explicit_retry(
    monkeypatch,
):
    connection = Mock()
    connection.execute.return_value = _result()
    _patch_engine(monkeypatch, connection)

    failed = {
        "id": 7,
        "sha256": "a" * 64,
        "status": "failed",
    }

    monkeypatch.setattr(
        submissions,
        "find_by_hash",
        lambda sha256, conn=None: None,
    )
    monkeypatch.setattr(
        submissions,
        "_latest_by_hash",
        lambda sha256, conn=None: failed,
    )

    with pytest.raises(submissions.ResubmissionRequired):
        submissions.register_submission(
            "thread-2",
            "a" * 64,
            "invoice.txt",
        )


def test_register_submission_rejects_different_hash(
    monkeypatch,
):
    connection = Mock()
    connection.execute.return_value = _result()
    _patch_engine(monkeypatch, connection)

    monkeypatch.setattr(
        submissions,
        "find_by_hash",
        lambda sha256, conn=None: None,
    )
    monkeypatch.setattr(
        submissions,
        "_latest_by_hash",
        lambda sha256, conn=None: None,
    )
    monkeypatch.setattr(
        submissions,
        "get_by_id",
        lambda submission_id, conn=None: {
            "id": submission_id,
            "sha256": "b" * 64,
            "status": "failed",
        },
    )

    with pytest.raises(submissions.HashMismatch):
        submissions.register_submission(
            "thread-2",
            "a" * 64,
            "invoice.txt",
            resubmit_of=7,
        )


def test_set_status_updates_processing_and_attempts(
    monkeypatch,
):
    connection = Mock()
    connection.execute.side_effect = [
        _result(row={
            "id": 1,
            "thread_id": "thread-1",
            "status": "queued",
        }),
        _result(rowcount=1),
        _result(row={
            "id": 1,
            "thread_id": "thread-1",
            "status": "processing",
            "attempts": 1,
        }),
    ]
    _patch_engine(monkeypatch, connection)

    row = submissions.set_status("thread-1", "processing")

    assert row["status"] == "processing"
    assert row["attempts"] == 1

    update_params = connection.execute.call_args_list[1].args[1]
    assert update_params["attempt_increment"] == 1


def test_set_status_rejects_invalid_transition(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(row={
        "id": 1,
        "thread_id": "thread-1",
        "status": "completed",
    })
    _patch_engine(monkeypatch, connection)

    with pytest.raises(submissions.SubmissionError):
        submissions.set_status("thread-1", "processing")

    connection.execute.assert_called_once()


def test_set_status_rejects_concurrent_change(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(row={
            "id": 1,
            "thread_id": "thread-1",
            "status": "queued",
        }),
        _result(rowcount=0),
    ]
    _patch_engine(monkeypatch, connection)

    with pytest.raises(submissions.SubmissionError, match="changed unexpectedly"):
        submissions.set_status("thread-1", "processing")

    assert connection.execute.call_count == 2


def test_claim_for_recovery_returns_updated_row(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(rowcount=1),
        _result(row={
            "id": 1,
            "thread_id": "thread-1",
            "status": "processing",
            "attempts": 2,
        }),
    ]
    _patch_engine(monkeypatch, connection)

    row = submissions.claim_for_recovery(
        "thread-1",
        "queued",
        "2026-01-01T00:00:00+00:00",
    )

    assert row["status"] == "processing"
    assert row["attempts"] == 2


def test_claim_for_recovery_returns_none_when_claim_lost(
    monkeypatch,
):
    connection = Mock()
    connection.execute.return_value = _result(rowcount=0)
    _patch_engine(monkeypatch, connection)

    assert submissions.claim_for_recovery(
        "thread-1",
        "queued",
        "2026-01-01T00:00:00+00:00",
    ) is None


def test_list_recoverable_returns_rows(monkeypatch):
    rows = [
        {"id": 1, "status": "queued"},
        {"id": 2, "status": "processing"},
    ]
    connection = Mock()
    connection.execute.return_value = _result(rows=rows)
    _patch_engine(monkeypatch, connection)

    assert submissions.list_recoverable() == rows


def test_list_interrupted_returns_rows(monkeypatch):
    rows = [{"id": 1, "status": "queued"}]
    connection = Mock()
    connection.execute.return_value = _result(rows=rows)
    _patch_engine(monkeypatch, connection)

    assert submissions.list_interrupted() == rows


def test_caller_supplied_connection_is_reused():
    connection = Mock()
    connection.execute.return_value = _result(row={
        "id": 1,
        "thread_id": "thread-1",
    })

    result = submissions.get_by_thread(
        "thread-1",
        conn=connection,
    )

    assert result["id"] == 1
    connection.execute.assert_called_once()


# ---- fail_if_exhausted ---------------------------------------------------------------

def test_fail_if_exhausted_updates_matching_row(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(rowcount=1),
        _result(row={
            "id": 1,
            "thread_id": "thread-1",
            "status": "failed",
            "attempts": 3,
        }),
    ]
    _patch_engine(monkeypatch, connection)

    row = submissions.fail_if_exhausted(
        "thread-1",
        "queued",
        "2026-01-01T00:00:00+00:00",
        3,
    )

    assert row["status"] == "failed"
    assert row["attempts"] == 3
    assert connection.execute.call_count == 2

    update_params = connection.execute.call_args_list[0].args[1]
    assert update_params["thread_id"] == "thread-1"
    assert update_params["expected_status"] == "queued"
    assert update_params["expected_updated_at"] == "2026-01-01T00:00:00+00:00"
    assert update_params["expected_attempts"] == 3
    assert update_params["max_attempts"] == submissions.MAX_ATTEMPTS


def test_fail_if_exhausted_returns_none_when_update_loses_race(
    monkeypatch,
):
    connection = Mock()
    connection.execute.return_value = _result(rowcount=0)
    _patch_engine(monkeypatch, connection)

    result = submissions.fail_if_exhausted(
        "thread-1",
        "processing",
        "2026-01-01T00:00:00+00:00",
        3,
    )

    assert result is None
    connection.execute.assert_called_once()


def test_fail_if_exhausted_rejects_invalid_status(monkeypatch):
    connection = Mock()
    _patch_engine(monkeypatch, connection)

    with pytest.raises(submissions.SubmissionError):
        submissions.fail_if_exhausted(
            "thread-1",
            "completed",
            "2026-01-01T00:00:00+00:00",
            3,
        )

    connection.execute.assert_not_called()


def test_fail_if_exhausted_rejects_attempts_below_limit(monkeypatch):
    connection = Mock()
    _patch_engine(monkeypatch, connection)

    with pytest.raises(submissions.SubmissionError):
        submissions.fail_if_exhausted(
            "thread-1",
            "queued",
            "2026-01-01T00:00:00+00:00",
            2,
        )

    connection.execute.assert_not_called()
