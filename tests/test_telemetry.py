
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src import db_core, telemetry

MODEL = "gemini-3.5-flash-lite"


def _started():
    return datetime.now(UTC)


def _record(thread_id="thread-1", **kwargs):
    defaults = {
        "step": "extract",
        "model": MODEL,
        "attempt": 1,
        "outcome": "success",
        "started_at": _started(),
        "latency_ms": 100,
        "usage": {
            "prompt_tokens": 1000,
            "output_tokens": 500,
            "thinking_tokens": 0,
            "total_tokens": 1500,
        },
        "thread_id": thread_id,
    }
    defaults.update(kwargs)
    return telemetry.record_call(**defaults)


def _rows():
    connection = db_core.connect()
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM llm_calls ORDER BY id"
            ).fetchall()
        ]
    finally:
        connection.close()


def test_record_call_stores_success_and_cost(tmp_db):
    row_id = _record()

    assert row_id is not None

    rows = _rows()
    assert len(rows) == 1

    row = rows[0]
    assert row["thread_id"] == "thread-1"
    assert row["step"] == "extract"
    assert row["provider"] == telemetry.PROVIDER
    assert row["model"] == MODEL
    assert row["attempt"] == 1
    assert row["outcome"] == "success"
    assert row["prompt_tokens"] == 1000
    assert row["output_tokens"] == 500
    assert row["thinking_tokens"] == 0
    assert row["est_cost_usd"] == pytest.approx(0.00155)
    assert row["pricing_version"] is not None
    assert row["latency_ms"] == 100


def test_missing_thinking_tokens_produce_unknown_cost(tmp_db):
    _record(
        usage={
            "prompt_tokens": 1000,
            "output_tokens": 500,
            "thinking_tokens": None,
            "total_tokens": 1500,
        }
    )

    row = _rows()[0]
    assert row["prompt_tokens"] == 1000
    assert row["output_tokens"] == 500
    assert row["thinking_tokens"] is None
    assert row["est_cost_usd"] is None


def test_missing_usage_produces_unknown_cost(tmp_db):
    _record(usage={})

    row = _rows()[0]
    assert row["prompt_tokens"] is None
    assert row["output_tokens"] is None
    assert row["est_cost_usd"] is None


def test_unknown_model_has_no_estimated_cost(tmp_db):
    _record(model="unknown-model")

    row = _rows()[0]
    assert row["est_cost_usd"] is None
    assert row["pricing_version"] is None


def test_error_record_stores_type_and_safe_category_only(tmp_db):
    secret = "PRIVATE_PROVIDER_MESSAGE_API_KEY=super-secret"
    error = RuntimeError(secret)

    _record(
        outcome="retryable_error",
        error=error,
    )

    row = _rows()[0]
    assert row["outcome"] == "retryable_error"
    assert row["error_type"] == "RuntimeError"
    assert row["error_category"] == "client_or_validation_error"

    assert secret not in str(row)
    assert "super-secret" not in str(row)


def test_http_error_category_is_safe(tmp_db):
    error = RuntimeError("private response contents")
    error.status_code = 429

    _record(
        outcome="retryable_error",
        error=error,
    )

    row = _rows()[0]
    assert row["error_category"] == "rate_limited"
    assert row["error_type"] == "RuntimeError"


def test_bind_thread_sets_and_restores_context():
    assert telemetry.current_thread_id() is None

    with telemetry.bind_thread("outer"):
        assert telemetry.current_thread_id() == "outer"

        with telemetry.bind_thread("inner"):
            assert telemetry.current_thread_id() == "inner"

        assert telemetry.current_thread_id() == "outer"

    assert telemetry.current_thread_id() is None


def test_bind_thread_restores_context_after_exception():
    with pytest.raises(RuntimeError):
        with telemetry.bind_thread("temporary"):
            raise RuntimeError("expected test failure")

    assert telemetry.current_thread_id() is None


def test_record_call_uses_bound_thread_id(tmp_db):
    with telemetry.bind_thread("workflow-123"):
        _record(thread_id=None)

    assert _rows()[0]["thread_id"] == "workflow-123"


def test_calls_for_thread_returns_only_matching_rows(tmp_db):
    _record(thread_id="first")
    _record(thread_id="second")

    rows = telemetry.calls_for_thread("first")

    assert len(rows) == 1
    assert rows[0]["thread_id"] == "first"


def test_calls_for_thread_returns_empty_list_for_unknown_thread(tmp_db):
    assert telemetry.calls_for_thread("missing") == []


def test_usage_from_response_preserves_zero_and_missing_values():
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=100,
            candidates_token_count=50,
            thoughts_token_count=0,
            total_token_count=150,
        )
    )

    usage = telemetry.usage_from_response(response)

    assert usage == {
        "prompt_tokens": 100,
        "output_tokens": 50,
        "thinking_tokens": 0,
        "total_tokens": 150,
    }


def test_usage_from_response_handles_missing_metadata():
    assert telemetry.usage_from_response(SimpleNamespace()) == {
        "prompt_tokens": None,
        "output_tokens": None,
        "thinking_tokens": None,
        "total_tokens": None,
    }


def test_usage_from_response_rejects_invalid_counts():
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=True,
            candidates_token_count=-1,
            thoughts_token_count="10",
            total_token_count=100,
        )
    )

    usage = telemetry.usage_from_response(response)

    assert usage == {
        "prompt_tokens": None,
        "output_tokens": None,
        "thinking_tokens": None,
        "total_tokens": 100,
    }


def test_telemetry_failure_does_not_raise(tmp_db, monkeypatch):
    def fail_connect():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db_core, "connect", fail_connect)

    result = _record()

    assert result is None


def test_calls_for_thread_handles_missing_table(tmp_path, monkeypatch):
    path = tmp_path / "without_telemetry.db"
    import sqlite3

    connection = sqlite3.connect(path)
    connection.close()

    monkeypatch.setattr(db_core, "DB_PATH", path)

    assert telemetry.calls_for_thread("thread-1") == []


def test_concurrent_threads_do_not_mix_context():
    def get_bound_thread(thread_id):
        with telemetry.bind_thread(thread_id):
            return telemetry.current_thread_id()

    thread_ids = [f"thread-{number}" for number in range(20)]

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(get_bound_thread, thread_ids))

    assert results == thread_ids
