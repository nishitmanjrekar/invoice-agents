"""LLM call telemetry (table created by migration 004).

Telemetry failures must never interrupt invoice processing. Each write uses
its own database connection, and workflow IDs travel in a ContextVar so
concurrent workflows cannot mix rows.

Raw provider error messages and invoice/document content are never stored.
"""
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from src import db_core, pricing

logger = logging.getLogger(__name__)

PROVIDER = "google-gemini"
OUTCOMES = ("success", "retryable_error", "error", "invalid_response")

_thread_id = ContextVar("llm_thread_id", default=None)

def _postgres():
    return db_core.get_database_backend() == "postgresql"


def _get_postgres_telemetry():
    from src import telemetry_postgres
    return telemetry_postgres


def current_thread_id():
    """Return the workflow ID bound to the current execution context."""
    return _thread_id.get()


@contextmanager
def bind_thread(thread_id):
    """Associate LLM calls inside this block with a workflow thread."""
    token = _thread_id.set(str(thread_id) if thread_id else None)
    try:
        yield
    finally:
        _thread_id.reset(token)


def _count(value):
    """Accept non-negative integer token counts; reject bool and other types."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def usage_from_response(response):
    """Extract Gemini token counts. Missing or invalid values remain None."""
    metadata = getattr(response, "usage_metadata", None)
    return {
        "prompt_tokens": _count(
            getattr(metadata, "prompt_token_count", None)
        ),
        "output_tokens": _count(
            getattr(metadata, "candidates_token_count", None)
        ),
        "thinking_tokens": _count(
            getattr(metadata, "thoughts_token_count", None)
        ),
        "total_tokens": _count(
            getattr(metadata, "total_token_count", None)
        ),
    }


def _safe_error_category(error, supplied=None):
    """Return a generic category without inspecting or storing error text."""
    if supplied:
        return str(supplied)[:50]

    if error is None:
        return None

    status_code = (
        getattr(error, "status_code", None)
        or getattr(error, "code", None)
    )
    if status_code is None:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)

    if status_code == 408:
        return "timeout"
    if status_code == 429:
        return "rate_limited"
    if status_code in {500, 502, 503, 504}:
        return "provider_unavailable"
    if status_code in {401, 403}:
        return "authentication_or_permission"
    if status_code == 400:
        return "invalid_request"
    if status_code is not None:
        return "provider_error"

    return "client_or_validation_error"


def _table_exists(connection):
    return connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'llm_calls'
        """
    ).fetchone() is not None


def record_call(
    *,
    step,
    model,
    attempt,
    outcome,
    started_at,
    latency_ms,
    usage=None,
    error=None,
    error_category=None,
    thread_id=None,
    provider=PROVIDER,
):
    """Store one LLM attempt; return its row ID or None if not stored.

    This function deliberately swallows telemetry errors so that a logging
    problem cannot break invoice extraction or workflow execution.
    """
    connection = None

    try:
        if outcome not in OUTCOMES:
            raise ValueError("Unknown telemetry outcome")

        if _postgres():
            return _get_postgres_telemetry().record_call(
                step=step,
                model=model,
                attempt=attempt,
                outcome=outcome,
                started_at=started_at,
                latency_ms=latency_ms,
                usage=usage,
                error=error,
                error_category=error_category,
                thread_id=thread_id,
                provider=provider,
            )

        usage = usage or {}
        prompt = _count(usage.get("prompt_tokens"))
        output = _count(usage.get("output_tokens"))
        thinking = _count(usage.get("thinking_tokens"))
        total = _count(usage.get("total_tokens"))

        cost = pricing.estimate_cost_usd(
            model, prompt, output, thinking
        )
        rates = pricing.get_rates(model)

        error_type = type(error).__name__ if error is not None else None
        category = _safe_error_category(error, error_category)

        resolved_thread_id = thread_id or current_thread_id()
        created_at = datetime.now(UTC).isoformat()
        started_at_value = started_at.isoformat()

        connection = db_core.connect()

        if not _table_exists(connection):
            logger.debug("llm_calls table not present; telemetry skipped")
            return None

        cursor = connection.execute(
            """
            INSERT INTO llm_calls (
                thread_id, step, provider, model, attempt, outcome,
                error_type, error_category, prompt_tokens, output_tokens,
                thinking_tokens, total_tokens, latency_ms, est_cost_usd,
                pricing_version, started_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_thread_id,
                step,
                provider,
                model,
                attempt,
                outcome,
                error_type,
                category,
                prompt,
                output,
                thinking,
                total,
                latency_ms,
                float(cost) if cost is not None else None,
                rates.version if rates is not None else None,
                started_at_value,
                created_at,
            ),
        )
        connection.commit()
        row_id = cursor.lastrowid

        logger.info(
            "llm_call %s",
            json.dumps(
                {
                    "thread_id": resolved_thread_id,
                    "step": step,
                    "model": model,
                    "attempt": attempt,
                    "outcome": outcome,
                    "latency_ms": latency_ms,
                    "prompt_tokens": prompt,
                    "output_tokens": output,
                    "est_cost_usd": (
                        float(cost) if cost is not None else None
                    ),
                }
            ),
        )
        return row_id

    except Exception as error:
        # Do not log the exception message: it may contain provider data.
        logger.error(
            "Could not record LLM telemetry (%s)",
            type(error).__name__,
        )
        return None

    finally:
        if connection is not None:
            connection.close()


def calls_for_thread(thread_id):
    """Return a workflow's attempts, oldest first; [] if migration 004 is absent."""
    if _postgres():
        return _get_postgres_telemetry().calls_for_thread(thread_id)
    connection = db_core.connect()
    try:
        if not _table_exists(connection):
            return []

        rows = connection.execute(
            """
            SELECT *
            FROM llm_calls
            WHERE thread_id = ?
            ORDER BY id
            """,
            (thread_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
