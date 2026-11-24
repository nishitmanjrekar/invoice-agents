from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from src import db, db_core, submissions
from src import submission_runner as runner
from src.agents.invoice_graph import build_invoice_graph
from src.extractors import FakeExtractor

SHA = "a" * 64


def make_open_graph(tmp_path, extractor):
    @contextmanager
    def open_graph():
        with SqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as saver:
            saver.setup()
            saver.serde = JsonPlusSerializer(allowed_msgpack_modules=[("src.schemas", "Invoice")])
            yield build_invoice_graph(
                checkpointer=saver, human_in_the_loop=True, use_mcp=False, extractor=extractor
            )

    return open_graph


@pytest.fixture
def env(seeded_db, tmp_path, make_invoice):
    extractor = FakeExtractor(make_invoice())
    return SimpleNamespace(
        extractor=extractor,
        open_graph=make_open_graph(tmp_path, extractor),
        tmp=tmp_path,
    )


def queue(env, thread_id="t1", sha=SHA):
    path = env.tmp / f"{thread_id}.txt"
    path.write_text("invoice text", encoding="utf-8")
    submissions.create_submission(thread_id, sha, path.name, stored_path=str(path))
    return str(path)


def cfg(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def force(thread_id, **columns):
    conn = db_core.connect()
    assignments = ", ".join(f"{k} = ?" for k in columns)
    conn.execute(f"UPDATE submissions SET {assignments} WHERE thread_id = ?",
                 (*columns.values(), thread_id))
    conn.commit()
    conn.close()


def invoice_status():
    with db.db_connection() as conn:
        return conn.execute("SELECT status FROM invoices").fetchone()[0]


# ---- classify ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "values,pending,expected",
    [
        ({}, (), "failed"),
        ({"status": "pending_approval"}, ("human_approval",), "awaiting_approval"),
        ({"error_stage": "extraction", "error_message": "boom"}, (), "failed"),
        ({"status": "processing_failed"}, (), "failed"),
        ({"status": "human_review", "review_case_id": 4}, (), "review_case"),
        ({"status": "approved"}, (), "completed"),
        ({"status": "pending_approval"}, ("validate_invoice",), "failed"),
    ],
)
def test_classify(values, pending, expected):
    state = SimpleNamespace(values=values, next=pending)
    assert runner.classify(state)[0] == expected


def test_classify_failure_message_includes_the_stage():
    state = SimpleNamespace(
        values={"error_stage": "extraction", "error_message": "Gemini unavailable"}, next=()
    )
    assert runner.classify(state) == ("failed", "extraction: Gemini unavailable")


# ---- run_submission ---------------------------------------------------------------------
def test_run_submission_pauses_for_approval_and_records_it(env):
    path = queue(env)
    assert runner.run_submission("t1", path, env.open_graph) == "awaiting_approval"
    row = submissions.get_by_thread("t1")
    assert row["status"] == "awaiting_approval"
    assert row["attempts"] == 1
    assert invoice_status() == "pending_approval"  # a human still has to decide
    assert len(env.extractor.calls) == 1


def test_run_submission_skips_a_row_that_is_not_queued(env):
    path = queue(env)
    submissions.set_status("t1", "processing")
    assert runner.run_submission("t1", path, env.open_graph) == "skipped"
    assert env.extractor.calls == []


def test_run_submission_skips_a_thread_without_a_ledger_row(env):
    assert runner.run_submission("ghost", "x.txt", env.open_graph) == "skipped"
    assert env.extractor.calls == []


def test_run_submission_records_a_graph_crash_as_failed(env):
    path = queue(env)

    @contextmanager
    def broken():
        raise RuntimeError("checkpoint database unavailable")
        yield

    assert runner.run_submission("t1", path, broken) == "failed"
    row = submissions.get_by_thread("t1")
    assert row["status"] == "failed"
    assert "checkpoint database unavailable" in row["error"]


def test_extraction_failure_is_recorded_as_failed(seeded_db, tmp_path):
    extractor = FakeExtractor(error=RuntimeError("Gemini unavailable"))
    environment = SimpleNamespace(extractor=extractor, tmp=tmp_path,
                                  open_graph=make_open_graph(tmp_path, extractor))
    path = queue(environment)
    assert runner.run_submission("t1", path, environment.open_graph) == "failed"
    assert "Gemini unavailable" in submissions.get_by_thread("t1")["error"]


# ---- recovery ---------------------------------------------------------------------------
def test_recovery_runs_a_queued_submission_that_never_started(env):
    queue(env)
    summary = runner.recover_submissions(env.open_graph)
    assert summary["recovered"] == 1
    row = submissions.get_by_thread("t1")
    assert row["status"] == "awaiting_approval"
    assert row["attempts"] == 1
    assert len(env.extractor.calls) == 1


def test_recovery_restarts_a_processing_row_with_no_checkpoint(env):
    queue(env)
    submissions.set_status("t1", "processing")  # crashed before the graph produced anything
    summary = runner.recover_submissions(env.open_graph)
    assert summary["recovered"] == 1
    row = submissions.get_by_thread("t1")
    assert row["status"] == "awaiting_approval"
    assert row["attempts"] == 2


def test_recovery_only_reconciles_a_workflow_already_paused_for_approval(env):
    path = queue(env)
    with env.open_graph() as graph:
        graph.invoke({"invoice_path": path}, cfg("t1"))
    submissions.set_status("t1", "processing")  # crash happened before the ledger update

    summary = runner.recover_submissions(env.open_graph)

    assert summary["recovered"] == 1
    assert submissions.get_by_thread("t1")["status"] == "awaiting_approval"
    assert len(env.extractor.calls) == 1  # nothing was re-run
    assert invoice_status() == "pending_approval"  # approval was NOT bypassed


def test_recovery_gives_up_after_max_attempts_without_running(env):
    queue(env)
    force("t1", status="processing", attempts=submissions.MAX_ATTEMPTS)
    summary = runner.recover_submissions(env.open_graph)
    assert summary["exhausted"] == 1
    row = submissions.get_by_thread("t1")
    assert row["status"] == "failed"
    assert "Gave up" in row["error"]
    assert env.extractor.calls == []


def test_recovery_leaves_fresh_rows_alone_during_the_grace_period(env):
    queue(env)
    summary = runner.recover_submissions(env.open_graph, grace_seconds=3600)
    assert summary["skipped"] == 1
    assert submissions.get_by_thread("t1")["status"] == "queued"
    assert env.extractor.calls == []


def test_recovery_skips_a_submission_running_in_this_process(env):
    queue(env)
    assert runner._enter("t1")
    try:
        summary = runner.recover_submissions(env.open_graph)
    finally:
        runner._leave("t1")
    assert summary["skipped"] == 1
    assert env.extractor.calls == []


def test_recovery_ignores_finished_submissions(env):
    queue(env)
    submissions.set_status("t1", "processing")
    submissions.set_status("t1", "completed")
    summary = runner.recover_submissions(env.open_graph)
    assert summary["examined"] == 0


def test_a_row_is_processed_only_once_when_worker_and_recovery_race(env):
    path = queue(env)
    row = submissions.get_by_thread("t1")
    # recovery claims first; the normal worker then finds the row no longer queued
    submissions.claim_for_recovery("t1", "queued", row["updated_at"])
    assert runner.run_submission("t1", path, env.open_graph) == "skipped"
    assert env.extractor.calls == []


@pytest.mark.parametrize(
    "changed_columns",
    [
        {"status": "completed"},
        {"updated_at": "2099-01-01T00:00:00+00:00"},
        {"attempts": submissions.MAX_ATTEMPTS + 1},
    ],
    ids=["status-changed", "timestamp-changed", "attempt-count-changed"],
)
def test_recovery_does_not_fail_a_row_that_changed_after_being_read(
    env, monkeypatch, changed_columns
):
    queue(env)
    force("t1", status="processing", attempts=submissions.MAX_ATTEMPTS)

    original_fail_if_exhausted = submissions.fail_if_exhausted

    def simulate_concurrent_update(
        thread_id,
        expected_status,
        expected_updated_at,
        expected_attempts,
        **kwargs,
    ):
        # Simulate another worker changing the row after recovery read it.
        force("t1", **changed_columns)

        # The real conditional update must reject the stale values.
        return original_fail_if_exhausted(
            thread_id,
            expected_status,
            expected_updated_at,
            expected_attempts,
            **kwargs,
        )

    monkeypatch.setattr(
        submissions,
        "fail_if_exhausted",
        simulate_concurrent_update,
    )

    summary = runner.recover_submissions(env.open_graph)

    row = submissions.get_by_thread("t1")

    assert summary["examined"] == 1
    assert summary["exhausted"] == 0
    assert summary["skipped"] == 1
    assert env.extractor.calls == []

    for column, expected_value in changed_columns.items():
        assert row[column] == expected_value
