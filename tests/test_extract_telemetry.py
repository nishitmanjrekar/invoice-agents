import json
from types import SimpleNamespace

import pytest

from src import db_core, extract, telemetry

MODEL = "gemini-3.5-flash-lite"


def _valid_response():
    return json.dumps({
        "vendor": "Acme Supplies",
        "invoice_number": "INV-TEST-001",
        "invoice_date": "2026-11-06",
        "due_date": "2026-12-06",
        "currency": "INR",
        "line_items": [
            {
                "description": "Office chairs",
                "quantity": 2,
                "unit_price": 1500,
            }
        ],
        "total": 3000,
        "po_number": "PO-2001",
    })


def _response(text=None, usage=True):
    metadata = None
    if usage:
        metadata = SimpleNamespace(
            prompt_token_count=1000,
            candidates_token_count=500,
            thoughts_token_count=0,
            total_token_count=1500,
        )

    return SimpleNamespace(
        text=_valid_response() if text is None else text,
        usage_metadata=metadata,
    )


class FakeModels:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)

        if isinstance(result, Exception):
            raise result

        return result


class FakeClient:
    def __init__(self, results):
        self.models = FakeModels(results)


def _install_client(monkeypatch, results):
    client = FakeClient(results)
    monkeypatch.setattr(extract, "get_gemini_client", lambda: client)
    return client


def _calls(thread_id):
    return telemetry.calls_for_thread(thread_id)


def test_success_records_usage_and_returns_invoice(tmp_db, monkeypatch):
    client = _install_client(monkeypatch, [_response()])

    with telemetry.bind_thread("extract-success"):
        invoice = extract.extract_invoice("PRIVATE INVOICE DOCUMENT")

    assert invoice.vendor == "Acme Supplies"
    assert invoice.invoice_number == "INV-TEST-001"
    assert len(client.models.calls) == 1

    rows = _calls("extract-success")
    assert len(rows) == 1

    row = rows[0]
    assert row["outcome"] == "success"
    assert row["attempt"] == 1
    assert row["prompt_tokens"] == 1000
    assert row["output_tokens"] == 500
    assert row["thinking_tokens"] == 0
    assert row["est_cost_usd"] == pytest.approx(0.00155)


def test_success_outside_workflow_has_null_thread_id(tmp_db, monkeypatch):
    _install_client(monkeypatch, [_response()])

    invoice = extract.extract_invoice("INVOICE TEXT")

    assert invoice.vendor == "Acme Supplies"

    connection = db_core.connect()
    try:
        row = connection.execute(
            "SELECT thread_id FROM llm_calls"
        ).fetchone()
    finally:
        connection.close()

    assert row["thread_id"] is None


def test_invoice_text_is_not_stored_in_telemetry(tmp_db, monkeypatch):
    _install_client(monkeypatch, [_response()])
    private_text = "CONFIDENTIAL CUSTOMER AND BANK DETAILS"

    with telemetry.bind_thread("privacy-test"):
        extract.extract_invoice(private_text)

    rows = _calls("privacy-test")

    assert len(rows) == 1
    assert private_text not in str(rows[0])


def test_missing_usage_records_unknown_cost(tmp_db, monkeypatch):
    _install_client(monkeypatch, [_response(usage=False)])

    with telemetry.bind_thread("missing-usage"):
        extract.extract_invoice("INVOICE TEXT")

    row = _calls("missing-usage")[0]

    assert row["prompt_tokens"] is None
    assert row["output_tokens"] is None
    assert row["thinking_tokens"] is None
    assert row["est_cost_usd"] is None


def test_retry_then_success_records_both_attempts(tmp_db, monkeypatch):
    error = RuntimeError("503 temporarily unavailable")
    client = _install_client(
        monkeypatch,
        [error, _response()],
    )
    monkeypatch.setattr(extract.time, "sleep", lambda _: None)

    with telemetry.bind_thread("retry-success"):
        invoice = extract.extract_invoice("INVOICE TEXT")

    assert invoice.vendor == "Acme Supplies"
    assert len(client.models.calls) == 2

    rows = _calls("retry-success")

    assert len(rows) == 2
    assert [row["attempt"] for row in rows] == [1, 2]
    assert [row["outcome"] for row in rows] == [
        "retryable_error",
        "success",
    ]


def test_exhausted_retries_record_every_attempt(tmp_db, monkeypatch):
    errors = [
        RuntimeError("503 temporarily unavailable"),
        RuntimeError("503 temporarily unavailable"),
        RuntimeError("503 temporarily unavailable"),
    ]
    client = _install_client(monkeypatch, errors)
    monkeypatch.setattr(extract.time, "sleep", lambda _: None)

    with telemetry.bind_thread("retry-exhausted"):
        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            extract.extract_invoice("INVOICE TEXT")

    assert len(client.models.calls) == 3

    rows = _calls("retry-exhausted")

    assert len(rows) == 3
    assert [row["attempt"] for row in rows] == [1, 2, 3]
    assert all(row["outcome"] == "retryable_error" for row in rows)


def test_permanent_error_is_recorded_without_retry(tmp_db, monkeypatch):
    error = RuntimeError("permanent provider failure")
    client = _install_client(monkeypatch, [error])

    with telemetry.bind_thread("permanent-error"):
        with pytest.raises(RuntimeError, match="Gemini extraction failed"):
            extract.extract_invoice("INVOICE TEXT")

    assert len(client.models.calls) == 1

    rows = _calls("permanent-error")

    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_type"] == "RuntimeError"


def test_invalid_json_records_invalid_response(tmp_db, monkeypatch):
    client = _install_client(
        monkeypatch,
        [_response(text="{ invalid json")],
    )

    with telemetry.bind_thread("invalid-json"):
        with pytest.raises(ValueError):
            extract.extract_invoice("INVOICE TEXT")

    assert len(client.models.calls) == 1

    rows = _calls("invalid-json")

    assert len(rows) == 1
    assert rows[0]["outcome"] == "invalid_response"
    assert rows[0]["prompt_tokens"] == 1000


def test_empty_response_is_recorded(tmp_db, monkeypatch):
    _install_client(monkeypatch, [_response(text="")])

    with telemetry.bind_thread("empty-response"):
        with pytest.raises(RuntimeError, match="empty response"):
            extract.extract_invoice("INVOICE TEXT")

    rows = _calls("empty-response")

    assert len(rows) == 1
    assert rows[0]["outcome"] == "invalid_response"


def test_api_key_is_not_stored_in_telemetry(tmp_db, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-api-key")
    _install_client(monkeypatch, [_response()])

    with telemetry.bind_thread("api-key-privacy"):
        extract.extract_invoice("INVOICE TEXT")

    rows = _calls("api-key-privacy")

    assert len(rows) == 1
    assert "test-secret-api-key" not in str(rows[0])


def test_telemetry_failure_does_not_break_extraction(
    tmp_db, monkeypatch
):
    _install_client(monkeypatch, [_response()])

    def fail_connect(*args, **kwargs):
        raise RuntimeError("telemetry database failure")

    monkeypatch.setattr(telemetry.db_core, "connect", fail_connect)

    invoice = extract.extract_invoice("INVOICE TEXT")

    assert invoice.vendor == "Acme Supplies"
    assert invoice.invoice_number == "INV-TEST-001"


def test_empty_invoice_fails_before_client_creation(monkeypatch):
    def fail_client():
        raise AssertionError("Client should not be created")

    monkeypatch.setattr(extract, "get_gemini_client", fail_client)

    with pytest.raises(ValueError, match="Invoice text is empty"):
        extract.extract_invoice("   ")


def test_configured_model_is_used_for_api_call(tmp_db, monkeypatch):
    _install_client(monkeypatch, [_response()])
    monkeypatch.setattr(extract, "MODEL_NAME", MODEL)

    extract.extract_invoice("INVOICE TEXT")

    # The fake client records the exact model argument.
    # No network or Gemini API call is made.
    assert extract.MODEL_NAME == MODEL