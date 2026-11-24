import pytest
from fastapi.testclient import TestClient

from src import db
from src.api import app

REPORT = {"checks": {"duplicate": {"existing_invoice_id": None}}, "risk_level": "high"}


@pytest.fixture
def client(seeded_db):
    return TestClient(app)


def _add(make_invoice, number, status):
    return db.save_invoice(make_invoice(invoice_number=number), status=status)


def _status(client, invoice_id):
    return client.get(f"/invoices/{invoice_id}").json()["invoice"]["status"]


# ---- existing routes keep their contract -------------------------------------------------
def test_health_reports_record_counts(client):
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert body["records"]["vendors"] == 1


def test_invoice_list_and_detail_shapes(client, make_invoice):
    invoice_id = _add(make_invoice, "INV-A", "pending_approval")
    listing = client.get("/invoices").json()
    assert listing["total"] == 1
    assert listing["invoices"][0]["vendor_name"] == "Acme Supplies"
    detail = client.get(f"/invoices/{invoice_id}").json()
    assert detail["invoice"]["extracted_data"]["invoice_number"] == "INV-A"
    assert detail["audit_history"][0]["action"] == "INVOICE_RECEIVED"


def test_review_case_rejection_route_still_works(client, make_invoice):
    case_id = db.create_review_case(make_invoice(), REPORT, "unknown_vendor")
    url = f"/review-cases/{case_id}/reject"
    assert client.post(url, json={"action": "reject", "reason": ""}).status_code == 400
    assert client.post(url, json={"action": "reject", "reason": "no"}).status_code == 200
    assert client.post(url, json={"action": "reject", "reason": "again"}).status_code == 409


# ---- new: reject-only endpoint for human_review invoices ---------------------------------
def test_reject_human_review_invoice(client, make_invoice):
    invoice_id = _add(make_invoice, "INV-H", "human_review")
    response = client.post(
        f"/invoices/{invoice_id}/reject", json={"action": "reject", "reason": "fraud suspected"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    detail = client.get(f"/invoices/{invoice_id}").json()
    assert detail["invoice"]["status"] == "rejected"
    assert detail["audit_history"][-1]["action"] == "INVOICE_REJECTED"


def test_approval_is_not_offered_for_human_review(client, make_invoice):
    invoice_id = _add(make_invoice, "INV-H", "human_review")
    response = client.post(f"/invoices/{invoice_id}/reject", json={"action": "approve"})
    assert response.status_code == 400
    assert _status(client, invoice_id) == "human_review"


def test_reject_requires_a_reason(client, make_invoice):
    invoice_id = _add(make_invoice, "INV-H", "human_review")
    response = client.post(
        f"/invoices/{invoice_id}/reject", json={"action": "reject", "reason": "   "}
    )
    assert response.status_code == 400
    assert _status(client, invoice_id) == "human_review"


def test_pending_invoices_must_use_the_workflow_route(client, make_invoice):
    invoice_id = _add(make_invoice, "INV-P", "pending_approval")
    response = client.post(
        f"/invoices/{invoice_id}/reject", json={"action": "reject", "reason": "x"}
    )
    assert response.status_code == 409
    assert _status(client, invoice_id) == "pending_approval"


def test_reject_unknown_invoice_returns_404(client):
    response = client.post("/invoices/9999/reject", json={"action": "reject", "reason": "x"})
    assert response.status_code == 404


def test_rejected_invoice_cannot_be_rejected_twice(client, make_invoice):
    invoice_id = _add(make_invoice, "INV-H", "human_review")
    url = f"/invoices/{invoice_id}/reject"
    assert client.post(url, json={"action": "reject", "reason": "a"}).status_code == 200
    assert client.post(url, json={"action": "reject", "reason": "b"}).status_code == 409