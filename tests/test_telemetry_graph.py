
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src import db, telemetry
from src.agents.invoice_graph import (
    build_invoice_graph,
    extract_invoice_node,
)
from src.extractors import FakeExtractor

TEXT = (
    "Vendor: Acme Supplies\n"
    "Invoice Number: INV-T-001\n"
    "Item: Office chairs\n"
    "Total: INR 3000\n"
)


@contextmanager
def open_graph(tmp_path, extractor):
    """Build the workflow with an offline extractor."""
    with SqliteSaver.from_conn_string(
        str(tmp_path / "checkpoints.sqlite")
    ) as saver:
        saver.setup()
        saver.serde = JsonPlusSerializer(
            allowed_msgpack_modules=[("src.schemas", "Invoice")]
        )
        yield build_invoice_graph(
            checkpointer=saver,
            human_in_the_loop=True,
            use_mcp=False,
            extractor=extractor,
        )


def _config(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def _invoice_file(tmp_path):
    path = tmp_path / "invoice.txt"
    path.write_text(TEXT, encoding="utf-8")
    return str(path)


def _status(invoice_id):
    with db.db_connection() as conn:
        return conn.execute(
            "SELECT status FROM invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()[0]


def test_fake_extractor_records_calls_and_returns_copies(make_invoice):
    extractor = FakeExtractor(make_invoice())

    first = extractor("a")
    second = extractor("b")

    assert first == second
    assert first is not second
    assert extractor.calls == ["a", "b"]


def test_fake_extractor_can_fail_on_demand():
    with pytest.raises(RuntimeError):
        FakeExtractor(error=RuntimeError("boom"))("text")


def test_clean_invoice_pauses_then_approves(
    seeded_db, tmp_path, make_invoice
):
    extractor = FakeExtractor(make_invoice())

    with open_graph(tmp_path, extractor) as graph:
        config = _config("t-approve")
        graph.invoke({"invoice_path": _invoice_file(tmp_path)}, config)

        paused = graph.get_state(config)

        assert paused.next == ("human_approval",)
        assert paused.values["status"] == "pending_approval"

        invoice_id = paused.values["invoice_id"]
        assert _status(invoice_id) == "pending_approval"

        graph.invoke(
            Command(resume={"action": "approve", "reason": "verified"}),
            config,
        )

        final = graph.get_state(config)

        assert final.next == ()
        assert final.values["status"] == "approved"
        assert _status(invoice_id) == "approved"

    assert len(extractor.calls) == 1


def test_rejection_needs_a_reason_then_succeeds(
    seeded_db, tmp_path, make_invoice
):
    with open_graph(tmp_path, FakeExtractor(make_invoice())) as graph:
        config = _config("t-reject")
        graph.invoke({"invoice_path": _invoice_file(tmp_path)}, config)

        invoice_id = graph.get_state(config).values["invoice_id"]

        with pytest.raises(ValueError):
            graph.invoke(
                Command(resume={"action": "reject", "reason": ""}),
                config,
            )

        assert _status(invoice_id) == "pending_approval"

        graph.invoke(
            Command(
                resume={
                    "action": "reject",
                    "reason": "wrong amount",
                }
            ),
            config,
        )

        assert _status(invoice_id) == "rejected"


def test_duplicate_goes_to_a_review_case_not_the_invoice_table(
    seeded_db, tmp_path, make_invoice
):
    db.save_invoice(make_invoice(), status="approved")

    with open_graph(tmp_path, FakeExtractor(make_invoice())) as graph:
        config = _config("t-duplicate")
        graph.invoke({"invoice_path": _invoice_file(tmp_path)}, config)
        state = graph.get_state(config)

    assert state.next == ()
    assert state.values["status"] == "human_review"
    assert state.values["invoice_id"] is None
    assert state.values["review_case_id"] is not None

    with db.db_connection() as conn:
        case = conn.execute(
            "SELECT case_type FROM invoice_review_cases"
        ).fetchone()
        invoices = conn.execute(
            "SELECT COUNT(*) FROM invoices"
        ).fetchone()[0]

    assert case["case_type"] == "duplicate"
    assert invoices == 1


def test_unknown_vendor_goes_to_a_review_case(
    seeded_db, tmp_path, make_invoice
):
    extractor = FakeExtractor(make_invoice(vendor="Ghost Ltd"))

    with open_graph(tmp_path, extractor) as graph:
        config = _config("t-unknown")
        graph.invoke({"invoice_path": _invoice_file(tmp_path)}, config)
        state = graph.get_state(config)

    assert state.values["status"] == "human_review"

    with db.db_connection() as conn:
        case = conn.execute(
            "SELECT case_type FROM invoice_review_cases"
        ).fetchone()

    assert case["case_type"] == "unknown_vendor"


def test_extraction_failure_is_recorded_not_raised(
    seeded_db, tmp_path
):
    extractor = FakeExtractor(
        error=RuntimeError("Gemini unavailable")
    )

    with open_graph(tmp_path, extractor) as graph:
        config = _config("t-fail")
        graph.invoke({"invoice_path": _invoice_file(tmp_path)}, config)
        state = graph.get_state(config)

    assert state.values["status"] == "processing_failed"
    assert state.values["error_stage"] == "extraction"

    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT invoice_id FROM audit_logs "
            "WHERE action = 'PROCESSING_FAILED'"
        ).fetchone()

    assert row is not None
    assert row["invoice_id"] is None


def test_graph_propagates_thread_id_to_extraction_telemetry(
    seeded_db, tmp_path, make_invoice
):
    observed_thread_ids = []

    def extractor(invoice_text):
        observed_thread_ids.append(
            telemetry.current_thread_id()
        )

        telemetry.record_call(
            step="extract",
            model="gemini-3.5-flash-lite",
            attempt=1,
            outcome="success",
            started_at=datetime.now(UTC),
            latency_ms=1,
        )

        return make_invoice()

    thread_id = "t-telemetry-graph"

    with open_graph(tmp_path, extractor) as graph:
        graph.invoke(
            {"invoice_path": _invoice_file(tmp_path)},
            _config(thread_id),
        )

    assert observed_thread_ids == [thread_id]

    rows = telemetry.calls_for_thread(thread_id)

    assert len(rows) == 1
    assert rows[0]["step"] == "extract"
    assert rows[0]["outcome"] == "success"


def test_extraction_node_without_config_preserves_compatibility(
    tmp_path, make_invoice
):
    observed_thread_ids = []

    def extractor(invoice_text):
        observed_thread_ids.append(
            telemetry.current_thread_id()
        )
        return make_invoice()

    result = extract_invoice_node(
        {"invoice_path": _invoice_file(tmp_path)},
        extractor=extractor,
    )

    assert observed_thread_ids == [None]
    assert result["invoice"].invoice_number == "INV-T-001"
    assert "error_stage" not in result
