import shutil
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from src import db, db_core, invoice_store, migrate, submissions
from src import submission_runner as runner
from src.agents.invoice_graph import build_invoice_graph, save_invoice_node
from src.extractors import FakeExtractor

SHA = "a" * 64
CONFIG = {"configurable": {"thread_id": "t1"}}


class Crash(BaseException):
    """Simulates the process dying; not caught by `except Exception`."""


def _state(invoice, status="pending_approval"):
    return {"invoice": invoice, "status": status}


def _scalar(sql, *args):
    conn = db_core.connect()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def _invoice_count():
    return _scalar("SELECT COUNT(*) FROM invoices")


def _audit_count(action):
    return _scalar("SELECT COUNT(*) FROM audit_logs WHERE action = ?", action)


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """A database at migration 002: invoices has no thread_id column."""
    folder = tmp_path / "upto002"
    folder.mkdir()
    for path in sorted(migrate.MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name[:3]) <= 2:
            shutil.copyfile(path, folder / path.name)
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(db_core, "DB_PATH", path)
    migrate.upgrade(path, create=True, migrations_dir=folder)
    with db.db_connection() as conn:
        conn.execute(
            "INSERT INTO vendors (vendor_name, is_approved, created_at) VALUES (?, 1, ?)",
            ("Acme Supplies", db.current_timestamp()),
        )
    return path


@pytest.fixture
def env(seeded_db, tmp_path, make_invoice):
    extractor = FakeExtractor(make_invoice())

    @contextmanager
    def open_graph():
        with SqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as saver:
            saver.setup()
            saver.serde = JsonPlusSerializer(allowed_msgpack_modules=[("src.schemas", "Invoice")])
            yield build_invoice_graph(
                checkpointer=saver, human_in_the_loop=True, use_mcp=False, extractor=extractor
            )

    path = tmp_path / "inv.txt"
    path.write_text("invoice text", encoding="utf-8")
    return SimpleNamespace(extractor=extractor, open_graph=open_graph, path=str(path))


# ---- invoice_store ----------------------------------------------------------------------
def test_first_save_records_the_thread_and_one_audit_row(seeded_db, make_invoice):
    invoice_id, created = invoice_store.save_invoice_once(make_invoice(), "pending_approval", "t1")
    assert created is True
    row = invoice_store.find_by_thread("t1")
    assert row["id"] == invoice_id
    assert row["status"] == "pending_approval"
    assert row["invoice_number"] == "INV-T-001"
    assert _audit_count("INVOICE_RECEIVED") == 1


def test_replaying_the_save_returns_the_same_invoice_and_writes_nothing(seeded_db, make_invoice):
    first, _ = invoice_store.save_invoice_once(make_invoice(), "pending_approval", "t1")
    second, created = invoice_store.save_invoice_once(make_invoice(), "pending_approval", "t1")
    assert (second, created) == (first, False)
    assert _invoice_count() == 1
    assert _audit_count("INVOICE_RECEIVED") == 1


def test_replay_after_a_human_decision_does_not_touch_the_status(seeded_db, make_invoice):
    invoice_id, _ = invoice_store.save_invoice_once(make_invoice(), "pending_approval", "t1")
    conn = db_core.connect()
    conn.execute("UPDATE invoices SET status = 'approved' WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()
    again, created = invoice_store.save_invoice_once(make_invoice(), "pending_approval", "t1")
    assert (again, created) == (invoice_id, False)
    assert invoice_store.find_by_thread("t1")["status"] == "approved"


def test_another_thread_with_the_same_invoice_is_still_a_duplicate(seeded_db, make_invoice):
    invoice_store.save_invoice_once(make_invoice(), "pending_approval", "t1")
    with pytest.raises(ValueError, match="Duplicate invoice"):
        invoice_store.save_invoice_once(make_invoice(), "pending_approval", "t2")
    assert _invoice_count() == 1
    assert _audit_count("INVOICE_RECEIVED") == 1


@pytest.mark.parametrize("approved,message", [(None, "Unapproved vendor"), (0, "not approved")])
def test_unapproved_vendors_are_refused_and_nothing_is_written(
    seeded_db, make_invoice, approved, message
):
    if approved is not None:
        conn = db_core.connect()
        conn.execute("UPDATE vendors SET is_approved = 0")
        conn.commit()
        conn.close()
        invoice = make_invoice()
    else:
        invoice = make_invoice(vendor="Ghost Ltd")
    with pytest.raises(ValueError, match=message):
        invoice_store.save_invoice_once(invoice, "pending_approval", "t1")
    assert _invoice_count() == 0
    assert _audit_count("INVOICE_RECEIVED") == 0


def test_has_thread_column_reflects_the_schema(seeded_db, legacy_db):
    assert invoice_store.has_thread_column() is False  # legacy_db is patched in last
    conn = db_core.connect(seeded_db)
    try:
        assert invoice_store.has_thread_column(conn=conn) is True
    finally:
        conn.close()


# ---- the node ---------------------------------------------------------------------------
def test_node_is_idempotent_per_thread(seeded_db, make_invoice):
    first = save_invoice_node(_state(make_invoice()), CONFIG)
    second = save_invoice_node(_state(make_invoice()), CONFIG)
    assert first == second
    assert isinstance(first["invoice_id"], int)
    assert _invoice_count() == 1
    assert _audit_count("INVOICE_SAVE_REQUIRES_REVIEW") == 0


def test_node_without_a_thread_keeps_the_original_behaviour(seeded_db, make_invoice):
    first = save_invoice_node(_state(make_invoice()))
    assert isinstance(first["invoice_id"], int)
    assert _scalar("SELECT thread_id FROM invoices") is None
    second = save_invoice_node(_state(make_invoice()))
    assert second["invoice_id"] is None
    assert second["status"] == "human_review"
    assert _invoice_count() == 1


def test_node_sends_a_real_duplicate_to_review(seeded_db, make_invoice):
    save_invoice_node(_state(make_invoice()), CONFIG)
    result = save_invoice_node(_state(make_invoice()), {"configurable": {"thread_id": "t2"}})
    assert result["invoice_id"] is None
    assert result["status"] == "human_review"
    assert _invoice_count() == 1


def test_node_falls_back_when_the_thread_column_does_not_exist(legacy_db, make_invoice):
    assert invoice_store.has_thread_column() is False
    result = save_invoice_node(_state(make_invoice()), CONFIG)
    assert isinstance(result["invoice_id"], int)
    assert _invoice_count() == 1


# ---- the workflow -----------------------------------------------------------------------
def test_replaying_from_before_the_save_node_creates_no_second_invoice(env):
    with env.open_graph() as graph:
        graph.invoke({"invoice_path": env.path}, CONFIG)
        rewound = [s for s in graph.get_state_history(CONFIG) if s.next == ("save_invoice",)]
        assert rewound, "no checkpoint found before save_invoice"
        graph.invoke(None, rewound[0].config)
        final = graph.get_state(CONFIG)

    assert _invoice_count() == 1
    assert _audit_count("INVOICE_RECEIVED") == 1
    assert _audit_count("INVOICE_SAVE_REQUIRES_REVIEW") == 0
    assert final.next == ("human_approval",)
    assert final.values["status"] == "pending_approval"
    assert _scalar("SELECT status FROM invoices") == "pending_approval"


def test_recovery_after_a_crash_following_the_commit_does_not_misroute(env, monkeypatch):
    submissions.create_submission("t1", SHA, "inv.txt", stored_path=env.path)
    submissions.set_status("t1", "processing")

    real = invoice_store.save_invoice_once
    armed = {"on": True}

    def crash_after_commit(*args, **kwargs):
        result = real(*args, **kwargs)
        if armed["on"]:
            armed["on"] = False
            raise Crash()
        return result

    monkeypatch.setattr(invoice_store, "save_invoice_once", crash_after_commit)

    with pytest.raises(BaseException):  # noqa: B017 - the simulated process death
        with env.open_graph() as graph:
            graph.invoke({"invoice_path": env.path}, CONFIG)

    assert _invoice_count() == 1  # the insert committed before the "crash"

    summary = runner.recover_submissions(env.open_graph)

    assert summary["recovered"] == 1
    row = submissions.get_by_thread("t1")
    assert row["status"] == "awaiting_approval"
    assert row["attempts"] == 2
    assert _invoice_count() == 1
    assert _scalar("SELECT status FROM invoices") == "pending_approval"  # human still decides
    assert _audit_count("INVOICE_SAVE_REQUIRES_REVIEW") == 0
    assert len(env.extractor.calls) == 1  # extraction was not repeated