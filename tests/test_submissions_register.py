import threading

import pytest

from src import db_core, submissions
from src.submissions import (
    HashMismatch,
    ResubmissionRequired,
    SubmissionError,
    SubmissionNotFound,
)

SHA = "a" * 64
OTHER = "b" * 64


def _reg(thread_id, sha=SHA, **kwargs):
    return submissions.register_submission(thread_id, sha, "inv.txt", **kwargs)


def _to(thread_id, *path):
    for status in path:
        submissions.set_status(thread_id, status)


def _count():
    conn = db_core.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    finally:
        conn.close()


def test_fresh_file_creates_a_queued_row(tmp_db):
    row, created = _reg("t1")
    assert created is True
    assert row["status"] == "queued"
    assert row["resubmission_of"] is None


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("processing",),
        ("processing", "awaiting_approval"),
        ("processing", "completed"),
        ("processing", "review_case"),
    ],
)
def test_same_hash_returns_the_existing_row_for_every_blocking_status(tmp_db, path):
    first, _ = _reg("t1")
    _to("t1", *path)
    again, created = _reg("t2")
    assert created is False
    assert again["thread_id"] == "t1"
    assert again["id"] == first["id"]
    assert _count() == 1


def test_failed_file_requires_explicit_resubmission(tmp_db):
    _reg("t1")
    _to("t1", "failed")
    with pytest.raises(ResubmissionRequired) as caught:
        _reg("t2")
    assert caught.value.existing["thread_id"] == "t1"
    assert _count() == 1


def test_explicit_resubmission_of_a_failed_row_creates_a_linked_row(tmp_db):
    original, _ = _reg("t1")
    _to("t1", "failed")
    row, created = _reg("t2", resubmit_of=original["id"])
    assert created is True
    assert row["resubmission_of"] == original["id"]
    assert row["status"] == "queued"
    assert submissions.get_by_thread("t1")["status"] == "failed"
    assert _count() == 2


@pytest.mark.parametrize("path", [("processing", "completed"), ("processing", "review_case")])
def test_completed_and_review_case_rows_can_be_resubmitted_explicitly(tmp_db, path):
    original, _ = _reg("t1")
    _to("t1", *path)
    row, created = _reg("t2", resubmit_of=original["id"])
    assert created is True
    assert row["resubmission_of"] == original["id"]
    assert submissions.get_by_thread("t1")["status"] == path[-1]


def test_resubmission_of_an_active_row_returns_the_active_row(tmp_db):
    original, _ = _reg("t1")
    row, created = _reg("t2", resubmit_of=original["id"])
    assert created is False
    assert row["thread_id"] == "t1"
    assert _count() == 1


def test_resubmission_must_be_the_same_file(tmp_db):
    original, _ = _reg("t1")
    _to("t1", "failed")
    with pytest.raises(HashMismatch):
        _reg("t2", OTHER, resubmit_of=original["id"])


def test_resubmission_of_unknown_id_is_refused(tmp_db):
    with pytest.raises(SubmissionNotFound):
        _reg("t1", resubmit_of=999)
    assert _count() == 0


def test_after_a_successful_resubmission_the_newest_blocking_row_wins(tmp_db):
    original, _ = _reg("t1")
    _to("t1", "failed")
    newer, _ = _reg("t2", resubmit_of=original["id"])
    _to("t2", "processing", "completed")
    again, created = _reg("t3")
    assert created is False
    assert again["id"] == newer["id"]


def test_concurrent_registration_of_the_same_file_creates_one_row(tmp_db):
    barrier = threading.Barrier(2)
    results, errors = [], []

    def worker(thread_id):
        try:
            barrier.wait()
            results.append(_reg(thread_id))
        except Exception as exc:  # pragma: no cover - reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert sorted(created for _, created in results) == [False, True]
    assert _count() == 1

# ---- claim_for_processing ---------------------------------------------------------------
def test_claim_for_processing_claims_a_queued_row_once(tmp_db):
    _reg("t1")

    claimed = submissions.claim_for_processing("t1")

    assert claimed["status"] == "processing"
    assert claimed["attempts"] == 1

    # A second worker cannot claim the same submission.
    assert submissions.claim_for_processing("t1") is None


def test_claim_for_processing_respects_the_attempt_cap(tmp_db):
    _reg("t1")

    conn = db_core.connect()
    try:
        conn.execute(
            "UPDATE submissions SET attempts = ? WHERE thread_id = ?",
            (submissions.MAX_ATTEMPTS, "t1"),
        )
        conn.commit()
    finally:
        conn.close()

    assert submissions.claim_for_processing("t1") is None

    row = submissions.get_by_thread("t1")
    assert row["status"] == "queued"
    assert row["attempts"] == submissions.MAX_ATTEMPTS


def test_claim_for_processing_does_not_claim_non_queued_rows(tmp_db):
    _reg("t1")
    submissions.set_status("t1", "processing")

    assert submissions.claim_for_processing("t1") is None

    row = submissions.get_by_thread("t1")
    assert row["status"] == "processing"
    assert row["attempts"] == 1

# ---- claim_for_recovery -----------------------------------------------------------------
def test_claim_takes_a_queued_row_once(tmp_db):
    row, _ = _reg("t1")
    claimed = submissions.claim_for_recovery("t1", "queued", row["updated_at"])
    assert claimed["status"] == "processing"
    assert claimed["attempts"] == 1
    assert submissions.claim_for_recovery("t1", "queued", row["updated_at"]) is None


def test_claim_takes_an_interrupted_processing_row_and_counts_an_attempt(tmp_db):
    _reg("t1")
    interrupted = submissions.set_status("t1", "processing")
    claimed = submissions.claim_for_recovery("t1", "processing", interrupted["updated_at"])
    assert claimed["attempts"] == 2
    assert submissions.claim_for_recovery("t1", "processing", interrupted["updated_at"]) is None


def test_claim_is_refused_with_a_stale_timestamp(tmp_db):
    _reg("t1")
    submissions.set_status("t1", "processing")
    assert submissions.claim_for_recovery("t1", "processing", "2000-01-01T00:00:00+00:00") is None


def test_claim_respects_the_attempt_cap(tmp_db):
    _reg("t1")
    conn = db_core.connect()
    conn.execute("UPDATE submissions SET attempts = 3")
    conn.commit()
    conn.close()
    row = submissions.get_by_thread("t1")
    assert submissions.claim_for_recovery("t1", "queued", row["updated_at"]) is None


def test_claim_refuses_other_statuses(tmp_db):
    _reg("t1")
    with pytest.raises(SubmissionError):
        submissions.claim_for_recovery("t1", "completed", "x")


def test_list_interrupted_ignores_the_attempt_cap_and_other_statuses(tmp_db):
    _reg("t1", SHA)
    _reg("t2", OTHER)
    _reg("t3", "c" * 64)
    _to("t2", "processing")
    _to("t3", "failed")
    conn = db_core.connect()
    conn.execute("UPDATE submissions SET attempts = 9 WHERE thread_id = 't2'")
    conn.commit()
    conn.close()
    assert [r["thread_id"] for r in submissions.list_interrupted()] == ["t1", "t2"]


def test_ledger_available_reflects_the_table(tmp_db):
    assert submissions.ledger_available() is True
    conn = db_core.connect()
    conn.execute("DROP TABLE submissions")
    conn.commit()
    conn.close()
    assert submissions.ledger_available() is False


# ---- fail_if_exhausted ---------------------------------------------------------------

def test_fail_if_exhausted_marks_matching_row_as_failed(tmp_db):
    _reg("t1")

    conn = db_core.connect()
    try:
        conn.execute(
            "UPDATE submissions SET attempts = ? WHERE thread_id = ?",
            (submissions.MAX_ATTEMPTS, "t1"),
        )
        conn.commit()
    finally:
        conn.close()

    row = submissions.get_by_thread("t1")

    failed = submissions.fail_if_exhausted(
        "t1",
        row["status"],
        row["updated_at"],
        row["attempts"],
    )

    assert failed["status"] == "failed"
    assert failed["attempts"] == submissions.MAX_ATTEMPTS
    assert "Gave up after" in failed["error"]


def test_fail_if_exhausted_rejects_stale_timestamp(tmp_db):
    _reg("t1")

    conn = db_core.connect()
    try:
        conn.execute(
            "UPDATE submissions SET attempts = ? WHERE thread_id = ?",
            (submissions.MAX_ATTEMPTS, "t1"),
        )
        conn.commit()
    finally:
        conn.close()

    row = submissions.get_by_thread("t1")

    result = submissions.fail_if_exhausted(
        "t1",
        row["status"],
        "2000-01-01T00:00:00+00:00",
        row["attempts"],
    )

    assert result is None
    assert submissions.get_by_thread("t1")["status"] == "queued"



def test_fail_if_exhausted_rejects_changed_attempt_count(tmp_db):
    _reg("t1")

    conn = db_core.connect()
    try:
        conn.execute(
            "UPDATE submissions SET attempts = ? WHERE thread_id = ?",
            (3, "t1"),
        )
        conn.commit()
    finally:
        conn.close()

    row = submissions.get_by_thread("t1")

    # The caller observed 2 attempts, but the database now has 3.
    # Use a limit of 2 so the expected count passes validation.
    result = submissions.fail_if_exhausted(
        "t1",
        row["status"],
        row["updated_at"],
        2,
        max_attempts=2,
    )

    assert result is None
    assert submissions.get_by_thread("t1")["status"] == "queued"



def test_fail_if_exhausted_rejects_changed_status(tmp_db):
    _reg("t1")

    conn = db_core.connect()
    try:
        conn.execute(
            "UPDATE submissions SET attempts = ? WHERE thread_id = ?",
            (submissions.MAX_ATTEMPTS, "t1"),
        )
        conn.commit()
    finally:
        conn.close()

    row = submissions.get_by_thread("t1")
    submissions.set_status("t1", "processing")

    result = submissions.fail_if_exhausted(
        "t1",
        row["status"],
        row["updated_at"],
        row["attempts"],
    )

    assert result is None
    assert submissions.get_by_thread("t1")["status"] == "processing"


def test_fail_if_exhausted_rejects_attempts_below_limit(tmp_db):
    _reg("t1")
    row = submissions.get_by_thread("t1")

    with pytest.raises(SubmissionError):
        submissions.fail_if_exhausted(
            "t1",
            row["status"],
            row["updated_at"],
            row["attempts"],
        )


def test_fail_if_exhausted_rejects_invalid_status(tmp_db):
    _reg("t1")

    with pytest.raises(SubmissionError):
        submissions.fail_if_exhausted(
            "t1",
            "completed",
            "2000-01-01T00:00:00+00:00",
            submissions.MAX_ATTEMPTS,
        )
