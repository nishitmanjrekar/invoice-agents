
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from src import telemetry_postgres as telemetry


def _result(row=None, rows=None, scalar=None):
    result = Mock()
    result.scalar_one.return_value = scalar
    result.mappings.return_value.all.return_value = rows or []
    result.mappings.return_value.first.return_value = row
    return result


def _engine(connection):
    @contextmanager
    def begin():
        yield connection

    @contextmanager
    def connect():
        yield connection

    return SimpleNamespace(begin=begin, connect=connect)


def _patch_engine(monkeypatch, connection):
    monkeypatch.setattr(
        telemetry,
        "get_postgres_engine",
        lambda: _engine(connection),
    )


def _record(**kwargs):
    defaults = {
        "step": "extract",
        "model": "gemini-3.5-flash-lite",
        "attempt": 1,
        "outcome": "success",
        "started_at": datetime.now(UTC),
        "latency_ms": 100,
        "usage": {
            "prompt_tokens": 1000,
            "output_tokens": 500,
            "thinking_tokens": 0,
            "total_tokens": 1500,
        },
        "thread_id": "thread-1",
    }
    defaults.update(kwargs)
    return telemetry.record_call(**defaults)


def test_record_call_inserts_and_returns_id(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(scalar="llm_calls"),
        _result(scalar=42),
    ]
    _patch_engine(monkeypatch, connection)

    row_id = _record()

    assert row_id == 42
    assert connection.execute.call_count == 2

    sql = str(connection.execute.call_args_list[1].args[0])
    params = connection.execute.call_args_list[1].args[1]

    assert "INSERT INTO llm_calls" in sql
    assert params["thread_id"] == "thread-1"
    assert params["prompt_tokens"] == 1000
    assert params["output_tokens"] == 500
    assert params["thinking_tokens"] == 0
    assert params["outcome"] == "success"
    assert params["est_cost_usd"] is not None


def test_record_call_uses_bound_thread_id(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(scalar="llm_calls"),
        _result(scalar=7),
    ]
    _patch_engine(monkeypatch, connection)

    from src import telemetry

    with telemetry.bind_thread("workflow-123"):
        row_id = _record(thread_id=None)

    assert row_id == 7
    params = connection.execute.call_args_list[1].args[1]
    assert params["thread_id"] == "workflow-123"


def test_record_call_stores_safe_error_information(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(scalar="llm_calls"),
        _result(scalar=8),
    ]
    _patch_engine(monkeypatch, connection)

    secret = "PRIVATE_API_KEY=super-secret"
    error = RuntimeError(secret)

    row_id = _record(
        outcome="retryable_error",
        error=error,
    )

    assert row_id == 8
    params = connection.execute.call_args_list[1].args[1]
    assert params["error_type"] == "RuntimeError"
    assert params["error_category"] == "client_or_validation_error"
    assert secret not in str(params)


def test_record_call_skips_missing_table(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(scalar=None)
    _patch_engine(monkeypatch, connection)

    assert _record() is None
    assert connection.execute.call_count == 1


def test_record_call_swallows_database_errors(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = RuntimeError(
        "database unavailable"
    )
    _patch_engine(monkeypatch, connection)

    assert _record() is None


def test_calls_for_thread_returns_rows(monkeypatch):
    rows = [
        {"id": 1, "thread_id": "thread-1"},
        {"id": 2, "thread_id": "thread-1"},
    ]
    connection = Mock()
    connection.execute.side_effect = [
        _result(scalar="llm_calls"),
        _result(rows=rows),
    ]
    _patch_engine(monkeypatch, connection)

    result = telemetry.calls_for_thread("thread-1")

    assert result == rows
    params = connection.execute.call_args_list[1].args[1]
    assert params["thread_id"] == "thread-1"


def test_calls_for_thread_returns_empty_when_table_missing(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(scalar=None)
    _patch_engine(monkeypatch, connection)

    assert telemetry.calls_for_thread("thread-1") == []


def test_calls_for_thread_swallows_database_errors(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = RuntimeError(
        "database unavailable"
    )
    _patch_engine(monkeypatch, connection)

    assert telemetry.calls_for_thread("thread-1") == []
