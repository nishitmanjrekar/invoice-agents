from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from src import api, db_core, submissions
from src.agents.invoice_graph import build_invoice_graph
from src.extractors import FakeExtractor

SHA = "a" * 64


@pytest.fixture
def env(seeded_db, tmp_path, monkeypatch, make_invoice):
    extractor = FakeExtractor(make_invoice())
    monkeypatch.setattr(api, "CHECKPOINT_DB", tmp_path / "cp.sqlite")
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path / "uploads")

    def fake_get_graph(saver):
        saver.serde = JsonPlusSerializer(allowed_msgpack_modules=[("src.schemas", "Invoice")])
        return build_invoice_graph(
            checkpointer=saver, human_in_the_loop=True, use_mcp=False, extractor=extractor
        )

    monkeypatch.setattr(api, "get_graph", fake_get_graph)
    return SimpleNamespace(client=TestClient(api.app), extractor=extractor,
                           uploads=tmp_path / "uploads")


def upload(env, content=b"invoice one", name="a.txt", **params):
    return env.client.post(
        "/invoices/process", files={"file": (name, content, "text/plain")}, params=params
    )


def rows():
    conn = db_core.connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM submissions ORDER BY id")]
    finally:
        conn.close()


def stored_files(env):
    return list(env.uploads.glob("*.txt")) if env.uploads.exists() else []


# ---- first upload -----------------------------------------------------------------------
def test_first_upload_is_recorded_processed_and_paused_for_approval(env):
    response = upload(env)
    body = response.json()
    assert response.status_code == 202
    assert body["status"] == "queued"
    assert body["already_submitted"] is False
    assert body["submission_id"] == 1

    row = submissions.get_by_thread(body["thread_id"])
    assert row["status"] == "awaiting_approval"
    assert row["attempts"] == 1
    assert row["sha256"] == submissions.hash_bytes(b"invoice one")
    assert len(env.extractor.calls) == 1
    assert len(stored_files(env)) == 1

    workflow = env.client.get(f"/workflows/{body['thread_id']}").json()
    assert workflow["approval_required"] is True
    assert workflow["submission_status"] == "awaiting_approval"


# ---- idempotency ------------------------------------------------------------------------
def test_same_file_returns_the_existing_submission_without_new_work(env):
    first = upload(env).json()
    second = upload(env)
    body = second.json()
    assert second.status_code == 200
    assert body["already_submitted"] is True
    assert body["thread_id"] == first["thread_id"]
    assert body["submission_id"] == first["submission_id"]
    assert body["status"] == "awaiting_approval"
    assert len(rows()) == 1
    assert len(env.extractor.calls) == 1
    assert len(stored_files(env)) == 1


def test_different_file_with_the_same_invoice_becomes_a_review_case(env):
    upload(env)
    second = upload(env, content=b"invoice two").json()
    assert submissions.get_by_thread(second["thread_id"])["status"] == "review_case"


# ---- failure and explicit resubmission ----------------------------------------------------
def test_failed_submission_needs_an_explicit_resubmission(env, make_invoice):
    env.extractor._error = RuntimeError("Gemini unavailable")
    first = upload(env)
    assert first.status_code == 202
    failed = submissions.get_by_thread(first.json()["thread_id"])
    assert failed["status"] == "failed"
    assert "Gemini unavailable" in failed["error"]

    blocked = upload(env)
    assert blocked.status_code == 409
    assert f"resubmit_of={failed['id']}" in blocked.json()["detail"]
    assert len(rows()) == 1

    env.extractor._error = None  # the outage is over
    retry = upload(env, resubmit_of=failed["id"])
    assert retry.status_code == 202
    new_row = submissions.get_by_thread(retry.json()["thread_id"])
    assert new_row["resubmission_of"] == failed["id"]
    assert new_row["status"] == "awaiting_approval"
    assert submissions.get_by_id(failed["id"])["status"] == "failed"
    assert len(rows()) == 2


def test_completed_and_review_case_submissions_can_be_resubmitted(env):
    first = upload(env).json()
    decision = env.client.post(
        f"/workflows/{first['thread_id']}/decision", json={"action": "approve", "reason": "ok"}
    )
    assert decision.status_code == 200
    assert submissions.get_by_thread(first["thread_id"])["status"] == "completed"

    second = upload(env, resubmit_of=first["submission_id"])
    assert second.status_code == 202
    row = submissions.get_by_thread(second.json()["thread_id"])
    assert row["status"] == "review_case"  # the invoice already exists: duplicate

    third = upload(env, resubmit_of=row["id"])
    assert third.status_code == 202
    assert [r["resubmission_of"] for r in rows()] == [None, 1, 2]


def test_resubmission_errors(env):
    first = upload(env).json()
    assert upload(env, content=b"other", resubmit_of=999).status_code == 404
    assert upload(env, content=b"other", resubmit_of=first["submission_id"]).status_code == 400
    active = upload(env, resubmit_of=first["submission_id"])
    assert active.status_code == 200
    assert active.json()["already_submitted"] is True
    assert len(rows()) == 1


# ---- ledger follows the human decision ---------------------------------------------------
def test_decision_closes_the_submission(env):
    first = upload(env).json()
    env.client.post(
        f"/workflows/{first['thread_id']}/decision", json={"action": "reject", "reason": "wrong"}
    )
    assert submissions.get_by_thread(first["thread_id"])["status"] == "completed"


# ---- reporting --------------------------------------------------------------------------
def test_failed_submission_without_a_checkpoint_is_reported_not_404(env):
    submissions.create_submission("tx", SHA, "f.txt")
    submissions.set_status("tx", "failed", error="disk full")
    body = env.client.get("/workflows/tx").json()
    assert body["status"] == "processing_failed"
    assert body["error_stage"] == "submission"
    assert body["error_message"] == "disk full"
    assert body["completed"] is True


def test_unknown_workflow_is_still_404(env):
    assert env.client.get("/workflows/ghost").status_code == 404


def test_workflow_list_includes_ledger_fields(env):
    upload(env)
    item = env.client.get("/workflows").json()["workflows"][0]
    assert item["submission_id"] == 1
    assert item["submission_status"] == "awaiting_approval"


# ---- validation and compatibility -------------------------------------------------------
def test_upload_validation_is_unchanged(env):
    assert upload(env, name="a.pdf").status_code == 400
    assert upload(env, content=b"   ").status_code == 400
    assert upload(env, content=b"\xff\xfe\x00").status_code == 400
    assert rows() == []
    assert stored_files(env) == []


def test_old_behaviour_is_kept_while_the_ledger_table_is_missing(env):
    conn = db_core.connect()
    conn.execute("DROP TABLE submissions")
    conn.commit()
    conn.close()

    response = upload(env)
    body = response.json()
    assert response.status_code == 202
    assert set(body) == {"message", "thread_id", "status"}
    assert len(env.extractor.calls) == 1
    assert env.client.get(f"/workflows/{body['thread_id']}").json()["approval_required"] is True


# ---- startup recovery -------------------------------------------------------------------
def test_startup_recovery_finishes_an_interrupted_submission(env, tmp_path):
    path = tmp_path / "interrupted.txt"
    path.write_text("invoice one", encoding="utf-8")
    submissions.create_submission("tr", SHA, "interrupted.txt", stored_path=str(path))
    submissions.set_status("tr", "processing")

    summary = api._recover_on_startup()

    assert summary["recovered"] == 1
    assert submissions.get_by_thread("tr")["status"] == "awaiting_approval"


def test_startup_recovery_is_skipped_without_the_ledger(env):
    conn = db_core.connect()
    conn.execute("DROP TABLE submissions")
    conn.commit()
    conn.close()
    assert api._recover_on_startup() is None

def test_authenticated_reviewer_is_recorded_as_workflow_actor(env, monkeypatch):
    from src import auth

    monkeypatch.setenv("INVOICE_AUTH", "enforce")
    monkeypatch.setenv("INVOICE_AUTH_SECRET", "test-secret-" + "x" * 32)

    auth.create_user("uploader1", "test-password", "uploader")
    auth.create_user("reviewer1", "test-password", "reviewer")

    def login_headers(username):
        response = env.client.post(
            "/auth/login",
            json={"username": username, "password": "test-password"},
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    upload_response = env.client.post(
        "/invoices/process",
        files={"file": ("a.txt", b"invoice one", "text/plain")},
        headers=login_headers("uploader1"),
    )
    assert upload_response.status_code == 202, upload_response.text

    thread_id = upload_response.json()["thread_id"]

    decision = env.client.post(
        f"/workflows/{thread_id}/decision",
        json={"action": "approve", "reason": "verified"},
        headers=login_headers("reviewer1"),
    )
    assert decision.status_code == 200

    conn = db_core.connect()
    try:
        audit = conn.execute(
            """
            SELECT actor FROM audit_logs
            WHERE action = 'INVOICE_APPROVED'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert audit["actor"] == "reviewer1"