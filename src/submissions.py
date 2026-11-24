"""Submissions ledger helpers (table created by migration 002).

Plain functions over app.db. No FastAPI or LangGraph imports. Every function
accepts an optional open connection so callers can share a transaction.
"""
import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime

from src import db_core

STATUSES = (
    "queued",
    "processing",
    "awaiting_approval",
    "completed",
    "failed",
    "review_case",
)

# A submission with one of these statuses blocks a new submission of the same file.
BLOCKING_STATUSES = (
    "queued",
    "processing",
    "awaiting_approval",
    "completed",
    "review_case",
)

# Still being worked on or waiting for a human: never resubmittable.
ACTIVE_STATUSES = ("queued", "processing", "awaiting_approval")

# Finished submissions that may be explicitly resubmitted.
RESUBMITTABLE_STATUSES = ("failed", "review_case", "completed")

# Allowed status moves. 'failed' may go back to 'queued' (explicit retry).
TRANSITIONS = {
    "queued": {"processing", "failed"},
    "processing": {"awaiting_approval", "completed", "review_case", "failed", "queued"},
    "awaiting_approval": {"completed", "failed"},
    "completed": set(),
    "review_case": set(),
    "failed": {"queued"},
}

MAX_ATTEMPTS = 3


class SubmissionError(Exception):
    pass


class SubmissionNotFound(SubmissionError):
    pass


class HashMismatch(SubmissionError):
    pass


class SubmissionConflict(SubmissionError):
    pass


class ResubmissionRequired(SubmissionError):
    """The same file failed before; processing it again must be explicit."""

    def __init__(self, existing):
        super().__init__(f"Submission {existing['id']} of this file failed.")
        self.existing = existing


def _now():
    return datetime.now(UTC).isoformat()

def _postgres():
    return db_core.get_database_backend() == "postgresql"


def _get_postgres_submissions():
    from src import submissions_postgres

    return submissions_postgres

@contextmanager
def _connection(conn):
    """Use the caller's connection, or open one and commit/close it."""
    if conn is not None:
        yield conn
        return

    own = db_core.connect()
    try:
        yield own
        own.commit()
    except Exception:
        own.rollback()
        raise
    finally:
        own.close()


def hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def ledger_available(*, conn=None) -> bool:
    """True when the submissions table exists (migration 002 applied)."""
    if _postgres():
        return _get_postgres_submissions().ledger_available(conn=conn)

    with _connection(conn) as c:
        return c.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'submissions'"
        ).fetchone() is not None


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
    """Insert a 'queued' submission and return its row as a dict."""

    if _postgres():
        return _get_postgres_submissions().create_submission(
            thread_id,
            sha256,
            filename,
            mime=mime,
            stored_path=stored_path,
            submitted_by=submitted_by,
            resubmission_of=resubmission_of,
            conn=conn,
        )
    now = _now()

    with _connection(conn) as c:
        if resubmission_of is not None and get_by_id(resubmission_of, conn=c) is None:
            raise SubmissionError(f"Submission {resubmission_of} does not exist.")

        cursor = c.execute(
            """
            INSERT INTO submissions (
                thread_id, sha256, filename, mime, stored_path, submitted_by,
                status, attempts, resubmission_of, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)
            """,
            (thread_id, sha256, filename, mime, stored_path, submitted_by,
             resubmission_of, now, now),
        )
        return get_by_id(cursor.lastrowid, conn=c)


def _row(row):
    return dict(row) if row is not None else None


def get_by_id(submission_id, *, conn=None):

    if _postgres():
        return _get_postgres_submissions().get_by_id(
            submission_id, conn=conn
        )
    with _connection(conn) as c:
        return _row(
            c.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
        )


def get_by_thread(thread_id, *, conn=None):
    if _postgres():
        return _get_postgres_submissions().get_by_thread(
            thread_id, conn=conn
        )
    with _connection(conn) as c:
        return _row(
            c.execute("SELECT * FROM submissions WHERE thread_id = ?", (thread_id,)).fetchone()
        )


def find_by_hash(sha256, *, conn=None):
    """Most recent submission with this hash that blocks a new one, or None."""
    if _postgres():
        return _get_postgres_submissions().find_by_hash(
            sha256, conn=conn
        )
    placeholders = ",".join("?" for _ in BLOCKING_STATUSES)

    with _connection(conn) as c:
        return _row(
            c.execute(
                f"""
                SELECT * FROM submissions
                WHERE sha256 = ? AND status IN ({placeholders})
                ORDER BY id DESC
                LIMIT 1
                """,
                (sha256, *BLOCKING_STATUSES),
            ).fetchone()
        )


def _latest_by_hash(sha256, *, conn=None):
    with _connection(conn) as c:
        return _row(
            c.execute(
                "SELECT * FROM submissions WHERE sha256 = ? ORDER BY id DESC LIMIT 1",
                (sha256,),
            ).fetchone()
        )


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
    Idempotent submit. Returns (row, created).

    The lookup and the insert run in one BEGIN IMMEDIATE transaction, so two
    simultaneous uploads of the same file cannot both create a row.
    See the behaviour table in the Phase 1 plan; summary:
      active or completed or review_case hash -> existing row, created=False
      only failed rows exist                  -> ResubmissionRequired
      resubmit_of given                       -> new linked row (same hash required,
                                                 original must be failed, review_case
                                                 or completed)
    """
    if _postgres():
        return _get_postgres_submissions().register_submission(
            thread_id,
            sha256,
            filename,
            mime=mime,
            stored_path=stored_path,
            submitted_by=submitted_by,
            resubmit_of=resubmit_of,
            conn=conn,
        )
    with _connection(conn) as c:
        if not c.in_transaction:
            c.execute("BEGIN IMMEDIATE")

        latest = find_by_hash(sha256, conn=c)

        if latest is not None and latest["status"] in ACTIVE_STATUSES:
            return latest, False

        if resubmit_of is None:
            if latest is not None:
                return latest, False

            failed = _latest_by_hash(sha256, conn=c)
            if failed is not None:
                raise ResubmissionRequired(failed)

            row = create_submission(
                thread_id, sha256, filename, mime=mime,
                stored_path=stored_path, submitted_by=submitted_by, conn=c,
            )
            return row, True

        original = get_by_id(resubmit_of, conn=c)

        if original is None:
            raise SubmissionNotFound(f"Submission {resubmit_of} does not exist.")

        if original["sha256"] != sha256:
            raise HashMismatch(
                f"Submission {resubmit_of} is a different file; "
                "a resubmission must upload the same document."
            )

        if original["status"] not in RESUBMITTABLE_STATUSES:
            raise SubmissionConflict(
                f"Submission {resubmit_of} is {original['status']} and cannot be resubmitted."
            )

        row = create_submission(
            thread_id, sha256, filename, mime=mime, stored_path=stored_path,
            submitted_by=submitted_by, resubmission_of=resubmit_of, conn=c,
        )
        return row, True


def set_status(thread_id, new_status, *, error=None, conn=None):
    """
    Move a submission to new_status if the transition is allowed.

    The update checks the current status in the WHERE clause, so two callers
    cannot both apply a move. Entering 'processing' increments attempts.
    Returns the updated row.
    """
    if _postgres():
        return _get_postgres_submissions().set_status(
            thread_id, new_status, error=error, conn=conn
        )
    if new_status not in STATUSES:
        raise SubmissionError(f"Unknown status: {new_status}")

    with _connection(conn) as c:
        current = get_by_thread(thread_id, conn=c)

        if current is None:
            raise SubmissionError(f"No submission for thread {thread_id}.")

        old_status = current["status"]

        if new_status not in TRANSITIONS[old_status]:
            raise SubmissionError(f"Cannot move from {old_status} to {new_status}.")

        stored_error = error if new_status == "failed" else None

        cursor = c.execute(
            """
            UPDATE submissions
            SET status = ?,
                error = ?,
                attempts = attempts + ?,
                updated_at = ?
            WHERE thread_id = ? AND status = ?
            """,
            (new_status, stored_error, 1 if new_status == "processing" else 0,
             _now(), thread_id, old_status),
        )

        if cursor.rowcount != 1:
            raise SubmissionError("Submission status changed unexpectedly. Try again.")

        return get_by_thread(thread_id, conn=c)


def claim_for_processing(thread_id, *, max_attempts=MAX_ATTEMPTS, conn=None):
    """
    Atomically claim a newly queued submission for normal processing.

    Succeeds only when the submission is still queued and below the
    attempt limit. Returns the updated row, or None if it cannot be claimed.
    """
    if _postgres():
        return _get_postgres_submissions().claim_for_processing(
            thread_id,
            max_attempts=max_attempts,
            conn=conn,
        )

    with _connection(conn) as c:
        cursor = c.execute(
            """
            UPDATE submissions
            SET status = 'processing',
                error = NULL,
                attempts = attempts + 1,
                updated_at = ?
            WHERE thread_id = ?
              AND status = 'queued'
              AND attempts < ?
            """,
            (_now(), thread_id, max_attempts),
        )

        if cursor.rowcount != 1:
            return None

        return get_by_thread(thread_id, conn=c)

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

    if _postgres():
        return _get_postgres_submissions().claim_for_recovery(
            thread_id,
            expected_status,
            expected_updated_at,
            max_attempts=max_attempts,
            conn=conn,
        )

    with _connection(conn) as c:
        cursor = c.execute(
            """
            UPDATE submissions
            SET status = 'processing',
                error = NULL,
                attempts = attempts + 1,
                updated_at = ?
            WHERE thread_id = ?
              AND status = ?
              AND updated_at = ?
              AND attempts < ?
            """,
            (
                _now(),
                thread_id,
                expected_status,
                expected_updated_at,
                max_attempts,
            ),
        )

        if cursor.rowcount != 1:
            return None

        return get_by_thread(thread_id, conn=c)

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

    if _postgres():
        return _get_postgres_submissions().fail_if_exhausted(
            thread_id,
            expected_status,
            expected_updated_at,
            expected_attempts,
            max_attempts=max_attempts,
            conn=conn,
        )

    with _connection(conn) as c:
        cursor = c.execute(
            """
            UPDATE submissions
            SET status = 'failed',
                error = ?,
                updated_at = ?
            WHERE thread_id = ?
              AND status = ?
              AND updated_at = ?
              AND attempts = ?
              AND attempts >= ?
            """,
            (
                (
                    f"Gave up after {expected_attempts} attempts; "
                    "the workflow never finished."
                ),
                _now(),
                thread_id,
                expected_status,
                expected_updated_at,
                expected_attempts,
                max_attempts,
            ),
        )

        if cursor.rowcount != 1:
            return None

        return get_by_thread(thread_id, conn=c)

def list_interrupted(*, conn=None):
    """All queued or processing submissions regardless of attempts, oldest first."""
    if _postgres():
        return _get_postgres_submissions().list_interrupted(conn=conn)
    with _connection(conn) as c:
        rows = c.execute(
            "SELECT * FROM submissions WHERE status IN ('queued', 'processing') ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]



def list_recoverable(*, max_attempts=MAX_ATTEMPTS, conn=None):
    """Queued or processing submissions below the attempt limit, oldest first."""
    if _postgres():
        return _get_postgres_submissions().list_recoverable(
            max_attempts=max_attempts,
            conn=conn,
        )

    with _connection(conn) as c:
        rows = c.execute(
            """
            SELECT *
            FROM submissions
            WHERE status IN ('queued', 'processing')
              AND attempts < ?
            ORDER BY id
            """,
            (max_attempts,),
        ).fetchall()

        return [dict(row) for row in rows]
