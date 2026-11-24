import sqlite3

import pytest

from src import db_core, submissions
from src.submissions import SubmissionError

SHA_A = "a" * 64
SHA_B = "b" * 64


def _make(thread_id="t1", sha=SHA_A, **kwargs):
    return submissions.create_submission(thread_id, sha, "inv.txt", **kwargs)


def _walk(thread_id, *statuses):
    for status in statuses:
        submissions.set_status(thread_id, status)


# ---- hashing ----------------------------------------------------------------------------
def test_hash_bytes_is_sha256_hex():
    assert submissions.hash_bytes(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_hash_differs_for_different_content():
    assert submissions.hash_bytes(b"a") != submissions.hash_bytes(b"b")


# ---- create / get -----------------------------------------------------------------------
def test_create_returns_a_queued_row(tmp_db):
    row = _make(mime="text/plain", stored_path="data/x.txt", submitted_by="tester")
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert row["error"] is None
    assert row["resubmission_of"] is None
    assert row["mime"] == "text/plain"
    assert row["submitted_by"] == "tester"
    assert row["created_at"] == row["updated_at"]


def test_get_by_id_and_thread(tmp_db):
    row = _make()
    assert submissions.get_by_id(row["id"])["thread_id"] == "t1"
    assert submissions.get_by_thread("t1")["id"] == row["id"]
    assert submissions.get_by_id(9999) is None
    assert submissions.get_by_thread("missing") is None


def test_duplicate_thread_id_is_rejected(tmp_db):
    _make("t1")
    with pytest.raises(sqlite3.IntegrityError):
        _make("t1")


def test_resubmission_links_to_the_original(tmp_db):
    original = _make("t1")
    again = _make("t2", resubmission_of=original["id"])
    assert again["resubmission_of"] == original["id"]
    assert submissions.get_by_id(original["id"])["resubmission_of"] is None


def test_resubmission_of_missing_row_is_rejected(tmp_db):
    with pytest.raises(SubmissionError):
        _make("t1", resubmission_of=9999)
    assert submissions.get_by_thread("t1") is None


# ---- hash lookup ------------------------------------------------------------------------
def test_find_by_hash_returns_none_when_unknown(tmp_db):
    assert submissions.find_by_hash(SHA_A) is None


@pytest.mark.parametrize(
    "path",
    [
        [],
        ["processing"],
        ["processing", "awaiting_approval"],
        ["processing", "completed"],
        ["processing", "review_case"],
    ],
)
def test_blocking_statuses_are_found(tmp_db, path):
    _make("t1")
    _walk("t1", *path)
    assert submissions.find_by_hash(SHA_A)["thread_id"] == "t1"


def test_failed_submission_does_not_block(tmp_db):
    _make("t1")
    submissions.set_status("t1", "failed", error="boom")
    assert submissions.find_by_hash(SHA_A) is None


def test_find_by_hash_ignores_other_hashes(tmp_db):
    _make("t1", SHA_A)
    assert submissions.find_by_hash(SHA_B) is None


def test_find_by_hash_returns_the_most_recent_blocking_row(tmp_db):
    first = _make("t1")
    submissions.set_status("t1", "processing")
    submissions.set_status("t1", "completed")
    second = _make("t2", resubmission_of=first["id"])
    assert submissions.find_by_hash(SHA_A)["thread_id"] == "t2"
    assert second["id"] > first["id"]


# ---- status transitions -----------------------------------------------------------------
def test_entering_processing_increments_attempts(tmp_db):
    _make()
    row = submissions.set_status("t1", "processing")
    assert row["status"] == "processing"
    assert row["attempts"] == 1


def test_other_transitions_do_not_change_attempts(tmp_db):
    _make()
    submissions.set_status("t1", "processing")
    row = submissions.set_status("t1", "awaiting_approval")
    assert row["attempts"] == 1


def test_requeue_then_reprocess_counts_a_second_attempt(tmp_db):
    _make()
    submissions.set_status("t1", "processing")
    submissions.set_status("t1", "queued")
    row = submissions.set_status("t1", "processing")
    assert row["attempts"] == 2


def test_failed_stores_the_error_and_a_later_move_clears_it(tmp_db):
    _make()
    row = submissions.set_status("t1", "failed", error="Gemini unavailable")
    assert row["status"] == "failed"
    assert row["error"] == "Gemini unavailable"
    row = submissions.set_status("t1", "queued")
    assert row["error"] is None


def test_error_is_ignored_for_non_failed_statuses(tmp_db):
    _make()
    row = submissions.set_status("t1", "processing", error="ignored")
    assert row["error"] is None


def test_updated_at_changes_and_created_at_does_not(tmp_db):
    row = _make()
    updated = submissions.set_status("t1", "processing")
    assert updated["created_at"] == row["created_at"]
    assert updated["updated_at"] >= row["updated_at"]


@pytest.mark.parametrize(
    "start_path,target",
    [
        ([], "completed"),
        ([], "awaiting_approval"),
        ([], "review_case"),
        (["processing", "completed"], "processing"),
        (["processing", "review_case"], "queued"),
        (["processing", "awaiting_approval"], "processing"),
        (["failed"], "processing"),
    ],
)
def test_invalid_transitions_are_refused_and_change_nothing(tmp_db, start_path, target):
    _make()
    _walk("t1", *start_path)
    before = submissions.get_by_thread("t1")
    with pytest.raises(SubmissionError):
        submissions.set_status("t1", target)
    assert submissions.get_by_thread("t1") == before


def test_unknown_status_is_refused(tmp_db):
    _make()
    with pytest.raises(SubmissionError):
        submissions.set_status("t1", "bogus")


def test_unknown_thread_is_refused(tmp_db):
    with pytest.raises(SubmissionError):
        submissions.set_status("missing", "processing")


def test_stale_update_is_refused_when_status_changed_underneath(tmp_db, monkeypatch):
    _make()
    real_get = submissions.get_by_thread
    calls = {"n": 0}

    def stale_get(thread_id, *, conn=None):
        row = real_get(thread_id, conn=conn)
        calls["n"] += 1
        if calls["n"] == 1:
            other = db_core.connect()
            other.execute("UPDATE submissions SET status = 'processing' WHERE thread_id = 't1'")
            other.commit()
            other.close()
        return row

    monkeypatch.setattr(submissions, "get_by_thread", stale_get)
    with pytest.raises(SubmissionError):
        submissions.set_status("t1", "processing")
    monkeypatch.setattr(submissions, "get_by_thread", real_get)
    assert submissions.get_by_thread("t1")["attempts"] == 0


# ---- recovery list ----------------------------------------------------------------------
def test_list_recoverable_returns_queued_and_processing_oldest_first(tmp_db):
    _make("t1", SHA_A)
    _make("t2", SHA_B)
    _make("t3", "c" * 64)
    _make("t4", "d" * 64)
    submissions.set_status("t2", "processing")
    _walk("t3", "processing", "completed")
    submissions.set_status("t4", "failed", error="x")
    assert [r["thread_id"] for r in submissions.list_recoverable()] == ["t1", "t2"]


def test_list_recoverable_respects_the_attempt_cap(tmp_db):
    _make("t1")
    for _ in range(3):
        submissions.set_status("t1", "processing")
        submissions.set_status("t1", "queued")
    assert submissions.get_by_thread("t1")["attempts"] == 3
    assert submissions.list_recoverable() == []
    assert [r["thread_id"] for r in submissions.list_recoverable(max_attempts=4)] == ["t1"]


# ---- shared transaction -----------------------------------------------------------------
def test_caller_supplied_connection_defers_the_commit(tmp_db):
    conn = db_core.connect()
    try:
        submissions.create_submission("t1", SHA_A, "inv.txt", conn=conn)
        assert submissions.get_by_thread("t1") is None  # not committed, other connection can't see it
        conn.commit()
        assert submissions.get_by_thread("t1") is not None
    finally:
        conn.close()


def test_caller_supplied_connection_can_roll_everything_back(tmp_db):
    conn = db_core.connect()
    try:
        submissions.create_submission("t1", SHA_A, "inv.txt", conn=conn)
        submissions.set_status("t1", "processing", conn=conn)
        conn.rollback()
    finally:
        conn.close()
    assert submissions.get_by_thread("t1") is None


def test_own_connection_rolls_back_on_error(tmp_db):
    _make("t1")
    with pytest.raises(sqlite3.IntegrityError):
        _make("t1")
    assert submissions.get_by_thread("t1")["status"] == "queued"