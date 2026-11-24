
"""Ledger-aware workflow runner and startup recovery.

Production PostgreSQL submissions retrieve invoice documents from Supabase Storage.
SQLite development and tests continue using local invoice paths.

open_graph is a zero-argument context manager factory yielding a compiled
LangGraph graph with a checkpointer.
"""

import logging
import threading
from datetime import UTC, datetime, timedelta

from src import object_storage, submissions
from src.db_core import get_database_backend
from src.submissions import MAX_ATTEMPTS, SubmissionError

logger = logging.getLogger(__name__)

_in_flight = set()
_lock = threading.Lock()


def _enter(thread_id):
    with _lock:
        if thread_id in _in_flight:
            return False
        _in_flight.add(thread_id)
        return True


def _leave(thread_id):
    with _lock:
        _in_flight.discard(thread_id)


def classify(state):
    """Map a finished or paused graph state to (ledger_status, error_or_None)."""
    values = state.values or {}
    pending = tuple(state.next or ())

    if not values:
        return "failed", "No workflow state was produced."

    if "human_approval" in pending:
        return "awaiting_approval", None

    if values.get("error_stage") or values.get("status") == "processing_failed":
        stage = values.get("error_stage") or "unknown"
        message = (
            values.get("error_message")
            or values.get("route_reason")
            or "Processing failed."
        )
        return "failed", f"{stage}: {message}"[:500]

    if values.get("review_case_id") is not None:
        return "review_case", None

    if pending:
        return "failed", (
            f"Workflow stopped before finishing (next: {', '.join(pending)})."
        )

    return "completed", None


def _invoke_initial_workflow(graph, invoice_path, config):
    """Invoke a new workflow using the configured document storage backend."""

    if get_database_backend() == "postgresql":
        # The graph receives a temporary local path while the original
        # invoice remains safely stored in Supabase Storage.
        with object_storage.download_invoice(invoice_path) as temporary_path:
            graph.invoke(
                {"invoice_path": str(temporary_path)},
                config,
            )
    else:
        # Preserve local SQLite development and test behaviour.
        graph.invoke({"invoice_path": invoice_path}, config)


def _execute(thread_id, invoice_path, open_graph):
    """Run, resume or reconcile a workflow, then record its outcome."""
    config = {"configurable": {"thread_id": thread_id}}

    try:
        with open_graph() as graph:
            state = graph.get_state(config)

            if not state.values:
                _invoke_initial_workflow(graph, invoice_path, config)

            elif state.next and "human_approval" not in state.next:
                # Resume an interrupted graph. Its previous state is already
                # checkpointed, so the original invoice file is not needed.
                graph.invoke(None, config)

            # Finished or paused for human approval: reconcile only.
            final = graph.get_state(config)

        status, error = classify(final)

    except Exception as exc:
        logger.exception("Workflow failed: %s", thread_id)
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:500]

    try:
        submissions.set_status(thread_id, status, error=error)
    except SubmissionError:
        logger.exception(
            "Could not record outcome %s for %s",
            status,
            thread_id,
        )

    logger.info("Submission %s finished as %s", thread_id, status)
    return status


def run_submission(thread_id, invoice_path, open_graph):
    """Run a freshly queued submission. Returns the outcome or 'skipped'."""
    if not _enter(thread_id):
        return "skipped"

    try:
        try:
            claimed = submissions.claim_for_processing(thread_id)
        except SubmissionError as exc:
            logger.warning("Not starting %s: %s", thread_id, exc)
            return "skipped"

        if claimed is None:
            logger.info(
                "Not starting %s: submission is no longer claimable.",
                thread_id,
            )
            return "skipped"

        return _execute(thread_id, invoice_path, open_graph)

    finally:
        _leave(thread_id)


def recover_submissions(
    open_graph,
    *,
    max_attempts=MAX_ATTEMPTS,
    grace_seconds=0.0,
):
    """
    Recover submissions interrupted by a restart.

    Each row is claimed with compare-and-swap before work begins.
    """
    summary = {
        "examined": 0,
        "recovered": 0,
        "exhausted": 0,
        "skipped": 0,
    }

    cutoff = datetime.now(UTC) - timedelta(seconds=grace_seconds)

    for row in submissions.list_interrupted():
        summary["examined"] += 1
        thread_id = row["thread_id"]

        if (
            grace_seconds > 0
            and datetime.fromisoformat(row["updated_at"]) > cutoff
        ):
            summary["skipped"] += 1
            continue

        if not _enter(thread_id):
            summary["skipped"] += 1
            continue

        try:
            if row["attempts"] >= max_attempts:
                try:
                    failed = submissions.fail_if_exhausted(
                        thread_id,
                        row["status"],
                        row["updated_at"],
                        row["attempts"],
                        max_attempts=max_attempts,
                    )
                except SubmissionError:
                    logger.exception(
                        "Could not mark exhausted submission %s as failed.",
                        thread_id,
                    )
                    summary["skipped"] += 1
                    continue

                if failed is None:
                    # The row changed after list_interrupted() returned it.
                    summary["skipped"] += 1
                else:
                    summary["exhausted"] += 1

                continue

            claimed = submissions.claim_for_recovery(
                thread_id,
                row["status"],
                row["updated_at"],
                max_attempts=max_attempts,
            )

            if claimed is None:
                summary["skipped"] += 1
                continue

            # PostgreSQL rows contain Supabase Storage object keys;
            # SQLite rows contain their original local file paths.
            _execute(
                thread_id,
                claimed["stored_path"],
                open_graph,
            )
            summary["recovered"] += 1

        finally:
            _leave(thread_id)

    logger.info("Submission recovery: %s", summary)
    return summary
