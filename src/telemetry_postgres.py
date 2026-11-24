
"""PostgreSQL implementation of LLM call telemetry."""

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import text

from src import pricing
from src.db_core import get_postgres_engine
from src.telemetry import (
    OUTCOMES,
    PROVIDER,
    _count,
    _safe_error_category,
    current_thread_id,
)

logger = logging.getLogger(__name__)


def _table_exists(connection):
    result = connection.execute(
        text("SELECT to_regclass('llm_calls')")
    )
    return result.scalar_one() is not None


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
    """Store one LLM attempt; return its row ID or None if not stored."""
    try:
        if outcome not in OUTCOMES:
            raise ValueError("Unknown telemetry outcome")

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

        engine = get_postgres_engine()

        with engine.begin() as connection:
            if not _table_exists(connection):
                logger.debug(
                    "llm_calls table not present; telemetry skipped"
                )
                return None

            result = connection.execute(
                text("""
                    INSERT INTO llm_calls (
                        thread_id, step, provider, model, attempt, outcome,
                        error_type, error_category, prompt_tokens,
                        output_tokens, thinking_tokens, total_tokens,
                        latency_ms, est_cost_usd, pricing_version,
                        started_at, created_at
                    )
                    VALUES (
                        :thread_id, :step, :provider, :model, :attempt,
                        :outcome, :error_type, :error_category,
                        :prompt_tokens, :output_tokens, :thinking_tokens,
                        :total_tokens, :latency_ms, :est_cost_usd,
                        :pricing_version, :started_at, :created_at
                    )
                    RETURNING id
                """),
                {
                    "thread_id": resolved_thread_id,
                    "step": step,
                    "provider": provider,
                    "model": model,
                    "attempt": attempt,
                    "outcome": outcome,
                    "error_type": error_type,
                    "error_category": category,
                    "prompt_tokens": prompt,
                    "output_tokens": output,
                    "thinking_tokens": thinking,
                    "total_tokens": total,
                    "latency_ms": latency_ms,
                    "est_cost_usd": (
                        float(cost) if cost is not None else None
                    ),
                    "pricing_version": (
                        rates.version if rates is not None else None
                    ),
                    "started_at": started_at_value,
                    "created_at": created_at,
                },
            )
            row_id = result.scalar_one()

        logger.info(
            "llm_call %s",
            json.dumps({
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
            }),
        )
        return row_id

    except Exception as error:
        # Never log exception messages; they may contain provider data.
        logger.error(
            "Could not record LLM telemetry (%s)",
            type(error).__name__,
        )
        return None


def calls_for_thread(thread_id):
    """Return a workflow's attempts, oldest first."""
    try:
        engine = get_postgres_engine()

        with engine.connect() as connection:
            if not _table_exists(connection):
                return []

            rows = connection.execute(
                text("""
                    SELECT *
                    FROM llm_calls
                    WHERE thread_id = :thread_id
                    ORDER BY id
                """),
                {"thread_id": thread_id},
            ).mappings().all()

            return [dict(row) for row in rows]

    except Exception as error:
        logger.error(
            "Could not read LLM telemetry (%s)",
            type(error).__name__,
        )
        return []
