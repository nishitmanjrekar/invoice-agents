import json

from src import db, review

REPORT = {"checks": {"duplicate": {"existing_invoice_id": None}}, "risk_level": "high"}


def _status(invoice_id):
    with db.db_connection() as conn:
        return conn.execute("SELECT status FROM invoices WHERE id = ?", (invoice_id,)).fetchone()[0]


def _audit(invoice_id):
    with db.db_connection() as conn:
        return conn.execute(
            "SELECT action, actor, details FROM audit_logs WHERE invoice_id = ? ORDER BY id",
            (invoice_id,),
        ).fetchall()


def test_approve_pending_invoice_updates_status_and_audit(seeded_db, make_invoice):
    invoice_id = db.save_invoice(make_invoice(), status="pending_approval")
    assert review.make_decision(invoice_id, "approve", "verified") is True
    assert _status(invoice_id) == "approved"
    last = _audit(invoice_id)[-1]
    assert last["action"] == "INVOICE_APPROVED"
    details = json.loads(last["details"])
    assert details["previous_status"] == "pending_approval"
    assert details["new_status"] == "approved"


def test_reject_requires_a_reason(seeded_db, make_invoice):
    invoice_id = db.save_invoice(make_invoice(), status="pending_approval")
    assert review.make_decision(invoice_id, "reject", "  ") is False
    assert _status(invoice_id) == "pending_approval"


def test_human_review_invoice_cannot_be_approved(seeded_db, make_invoice):
    invoice_id = db.save_invoice(make_invoice(), status="human_review")
    assert review.make_decision(invoice_id, "approve", "please") is False
    assert _status(invoice_id) == "human_review"


def test_human_review_invoice_can_be_rejected(seeded_db, make_invoice):
    invoice_id = db.save_invoice(make_invoice(), status="human_review")
    assert review.make_decision(invoice_id, "reject", "suspicious") is True
    assert _status(invoice_id) == "rejected"


def test_decided_invoice_cannot_be_decided_again(seeded_db, make_invoice):
    invoice_id = db.save_invoice(make_invoice(), status="approved")
    assert review.make_decision(invoice_id, "reject", "late") is False
    assert _status(invoice_id) == "approved"


def test_unknown_invoice_returns_false(seeded_db):
    assert review.make_decision(9999, "reject", "x") is False


def test_review_case_rejection_flow(seeded_db, make_invoice):
    case_id = db.create_review_case(make_invoice(), REPORT, "unknown_vendor")
    assert review.reject_review_case(case_id, "") is False
    assert review.reject_review_case(case_id, "not our vendor") is True
    assert review.reject_review_case(case_id, "again") is False
    with db.db_connection() as conn:
        row = conn.execute(
            "SELECT status, reviewer_reason FROM invoice_review_cases WHERE id = ?", (case_id,)
        ).fetchone()
    assert row["status"] == "rejected"
    assert row["reviewer_reason"] == "not our vendor"