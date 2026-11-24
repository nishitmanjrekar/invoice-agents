
"""PostgreSQL implementation of the submissions ledger."""

from contextlib import contextmanager

from sqlalchemy import text

from src.db_core import get_postgres_engine
from src.submissions import (
    ACTIVE_STATUSES,
    BLOCKING_STATUSES,
    MAX_ATTEMPTS,
    RESUBMITTABLE_STATUSES,
    STATUSES,
    TRANSITIONS,
    HashMismatch,
    ResubmissionRequired,
    SubmissionConflict,
    SubmissionError,
    SubmissionNotFound,
    _now,
)


@contextmanager
def _connection(conn=None):
    """Reuse a caller connection or open an automatically managed transaction."""
    if conn is not None:
        yield conn
        return

    engine = get_postgres_engine()
    with engine.begin() as connection:
        yield connection


def _row(row):
    return dict(row) if row is not None else None


def hash_bytes(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def ledger_available(*, conn=None) -> bool:
    with _connection(conn) as connection:
        result = connection.execute(
            text("SELECT to_regclass('submissions')")
        ).scalar_one()
        return result is not None


def get_by_id(submission_id, *, conn=None):
    with _connection(conn) as connection:
        row = connection.execute(
            text("SELECT * FROM submissions WHERE id = :submission_id"),
            {"submission_id": submission_id},
        ).mappings().first()
        return _row(row)


def get_by_thread(thread_id, *, conn=None):
    with _connection(conn) as connection:
        row = connection.execute(
            text("SELECT * FROM submissions WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        ).mappings().first()
        return _row(row)


def find_by_hash(sha256, *, conn=None):
    with _connection(conn) as connection:
        row = connection.execute(
            text("""
                SELECT *
                FROM submissions
                WHERE sha256 = :sha256
                  AND status = ANY(:statuses)
                ORDER BY id DESC
                LIMIT 1
            """),
            {
                "sha256": sha256,
                "statuses": list(BLOCKING_STATUSES),
            },
        ).mappings().first()
        return _row(row)


def _latest_by_hash(sha256, *, conn=None):
    with _connection(conn) as connection:
        row = connection.execute(
            text("""
                SELECT *
                FROM submissions
                WHERE sha256 = :sha256
                ORDER BY id DESC
                LIMIT 1
            """),
            {"sha256": sha256},
        ).mappings().first()
        return _row(row)


def create_submission(
    thread_id,
    sha256,
    filename,
    *,
    mime=None,
    stored_path=None,
    submitted_by=None,
    resubmission_of=None,
    conn=None,
):
    now = _now()

    with _connection(conn) as connection:
        if (
            resubmission_of is not None
            and get_by_id(resubmission_of, conn=connection) is None
        ):
            raise SubmissionError(
                f"Submission {resubmission_of} does not exist."
            )

        result = connection.execute(
            text("""
                INSERT INTO submissions (
                    thread_id, sha256, filename, mime, stored_path,
                    submitted_by, status, attempts, resubmission_of,
                    created_at, updated_at
                )
                VALUES (
                    :thread_id, :sha256, :filename, :mime, :stored_path,
                    :submitted_by, 'queued', 0, :resubmission_of,
                    :created_at, :updated_at
                )
                RETURNING id
            """),
            {
                "thread_id": thread_id,
                "sha256": sha256,
                "filename": filename,
                "mime": mime,
                "stored_path": stored_path,
                "submitted_by": submitted_by,
                "resubmission_of": resubmission_of,
                "created_at": now,
                "updated_at": now,
            },
        )

        submission_id = result.scalar_one()
        return get_by_id(submission_id, conn=connection)


def register_submission(
    thread_id,
    sha256,
    filename,
    *,
    mime=None,
    stored_path=None,
    submitted_by=None,
    resubmit_of=None,
    conn=None,
):
    """
    Idempotently register a submission.

    A transaction-scoped advisory lock serializes registrations for the
    same file hash, including when no matching row exists yet.
    """
    with _connection(conn) as connection:
        connection.execute(
            text("""
                SELECT pg_advisory_xact_lock(
                    hashtextextended(:sha256, 0)
                )
            """),
            {"sha256": sha256},
        )

        latest = find_by_hash(sha256, conn=connection)

        if latest is not None and latest["status"] in ACTIVE_STATUSES:
            return latest, False

        if resubmit_of is None:
            if latest is not None:
                return latest, False

            failed = _latest_by_hash(sha256, conn=connection)

            if failed is not None:
                raise ResubmissionRequired(failed)

            row = create_submission(
                thread_id,
                sha256,
                filename,
                mime=mime,
                stored_path=stored_path,
                submitted_by=submitted_by,
                conn=connection,
            )
            return row, True

        original = get_by_id(resubmit_of, conn=connection)

        if original is None:
            raise SubmissionNotFound(
                f"Submission {resubmit_of} does not exist."
            )

        if original["sha256"] != sha256:
            raise HashMismatch(
                f"Submission {resubmit_of} is a different file; "
                "a resubmission must upload the same document."
            )

        if original["status"] not in RESUBMITTABLE_STATUSES:
            raise SubmissionConflict(
                f"Submission {resubmit_of} is {original['status']} "
                "and cannot be resubmitted."
            )

        row = create_submission(
            thread_id,
            sha256,
            filename,
            mime=mime,
            stored_path=stored_path,
            submitted_by=submitted_by,
            resubmission_of=resubmit_of,
            conn=connection,
        )
        return row, True


def set_status(thread_id, new_status, *, error=None, conn=None):
    if new_status not in STATUSES:
        raise SubmissionError(f"Unknown status: {new_status}")

    with _connection(conn) as connection:
        current = connection.execute(
            text("""
                SELECT *
                FROM submissions
                WHERE thread_id = :thread_id
                FOR UPDATE
            """),
            {"thread_id": thread_id},
        ).mappings().first()

        current = _row(current)

        if current is None:
            raise SubmissionError(
                f"No submission for thread {thread_id}."
            )

        old_status = current["status"]

        if new_status not in TRANSITIONS[old_status]:
            raise SubmissionError(
                f"Cannot move from {old_status} to {new_status}."
            )

        stored_error = error if new_status == "failed" else None

        result = connection.execute(
            text("""
                UPDATE submissions
                SET status = :new_status,
                    error = :error,
                    attempts = attempts + :attempt_increment,
                    updated_at = :updated_at
                WHERE thread_id = :thread_id
                  AND status = :old_status
            """),
            {
                "new_status": new_status,
                "error": stored_error,
                "attempt_increment": (
                    1 if new_status == "processing" else 0
                ),
                "updated_at": _now(),
                "thread_id": thread_id,
                "old_status": old_status,
            },
        )

        if result.rowcount != 1:
            raise SubmissionError(
                "Submission status changed unexpectedly. Try again."
            )

        return get_by_thread(thread_id, conn=connection)


def claim_for_processing(
    thread_id,
    *,
    max_attempts=MAX_ATTEMPTS,
    conn=None,
):
    """
    Atomically claim a newly queued submission for normal processing.

    Returns the updated row, or None if it cannot be claimed.
    """
    with _connection(conn) as connection:
        result = connection.execute(
            text("""
                UPDATE submissions
                SET status = 'processing',
                    error = NULL,
                    attempts = attempts + 1,
                    updated_at = :updated_at
                WHERE thread_id = :thread_id
                  AND status = 'queued'
                  AND attempts < :max_attempts
            """),
            {
                "updated_at": _now(),
                "thread_id": thread_id,
                "max_attempts": max_attempts,
            },
        )

        if result.rowcount != 1:
            return None

        return get_by_thread(thread_id, conn=connection)

def claim_for_recovery(
    thread_id,
    expected_status,
    expected_updated_at,
    *,
    max_attempts=MAX_ATTEMPTS,
    conn=None,
):
    """Atomically claim an interrupted submission for recovery."""

    if expected_status not in ("queued", "processing"):
        raise SubmissionError(
            f"Cannot recover a submission with status {expected_status}."
        )

    with _connection(conn) as connection:
        result = connection.execute(
            text("""
                UPDATE submissions
                SET status = 'processing',
                    error = NULL,
                    attempts = attempts + 1,
                    updated_at = :updated_at
                WHERE thread_id = :thread_id
                  AND status = :expected_status
                  AND updated_at = :expected_updated_at
                  AND attempts < :max_attempts
            """),
            {
                "updated_at": _now(),
                "thread_id": thread_id,
                "expected_status": expected_status,
                "expected_updated_at": expected_updated_at,
                "max_attempts": max_attempts,
            },
        )

        if result.rowcount != 1:
            return None

        return get_by_thread(thread_id, conn=connection)

def fail_if_exhausted(
    thread_id,
    expected_status,
    expected_updated_at,
    expected_attempts,
    *,
    max_attempts=MAX_ATTEMPTS,
    conn=None,
):
    """Mark an exhausted submission failed only if its state is unchanged."""

    if expected_status not in ("queued", "processing"):
        raise SubmissionError(
            f"Cannot exhaust a submission with status {expected_status}."
        )

    if expected_attempts < max_attempts:
        raise SubmissionError(
            "Cannot fail a submission that has not exhausted its attempts."
        )

    with _connection(conn) as connection:
        result = connection.execute(
            text("""
                UPDATE submissions
                SET status = 'failed',
                    error = :error,
                    updated_at = :updated_at
                WHERE thread_id = :thread_id
                  AND status = :expected_status
                  AND updated_at = :expected_updated_at
                  AND attempts = :expected_attempts
                  AND attempts >= :max_attempts
            """),
            {
                "error": (
                    f"Gave up after {expected_attempts} attempts; "
                    "the workflow never finished."
                ),
                "updated_at": _now(),
                "thread_id": thread_id,
                "expected_status": expected_status,
                "expected_updated_at": expected_updated_at,
                "expected_attempts": expected_attempts,
                "max_attempts": max_attempts,
            },
        )

        if result.rowcount != 1:
            return None

        return get_by_thread(thread_id, conn=connection)

def list_recoverable(max_attempts=MAX_ATTEMPTS, *, conn=None):
    with _connection(conn) as connection:
        rows = connection.execute(
            text("""
                SELECT *
                FROM submissions
                WHERE status IN ('queued', 'processing')
                  AND attempts < :max_attempts
                ORDER BY id
            """),
            {"max_attempts": max_attempts},
        ).mappings().all()

        return [dict(row) for row in rows]


def list_interrupted(*, conn=None):
    with _connection(conn) as connection:
        rows = connection.execute(
            text("""
                SELECT *
                FROM submissions
                WHERE status IN ('queued', 'processing')
                ORDER BY id
            """)
        ).mappings().all()

        return [dict(row) for row in rows]
