from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from src import api, api_queries_postgres


@pytest.fixture
def postgres_query_engine(monkeypatch):
    """Use SQLite in-memory to exercise SQLAlchemy Core query contracts.

    No PostgreSQL server or production database is contacted. The SQL used by
    these read helpers is intentionally portable across both dialects.
    """

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    schema = [
        """
        CREATE TABLE submissions (
            id INTEGER PRIMARY KEY,
            thread_id TEXT,
            status TEXT
        )
        """,
        """
        CREATE TABLE vendors (
            id INTEGER PRIMARY KEY,
            vendor_name TEXT
        )
        """,
        """
        CREATE TABLE invoices (
            id INTEGER PRIMARY KEY,
            invoice_number TEXT,
            vendor_id INTEGER,
            invoice_date TEXT,
            currency TEXT,
            total REAL,
            po_number TEXT,
            status TEXT,
            created_at TEXT,
            extracted_data TEXT
        )
        """,
        """
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY,
            invoice_id INTEGER,
            action TEXT,
            actor TEXT,
            details TEXT,
            created_at TEXT
        )
        """,
        """
        CREATE TABLE invoice_review_cases (
            id INTEGER PRIMARY KEY,
            invoice_id INTEGER,
            case_type TEXT,
            reason TEXT,
            status TEXT,
            created_at TEXT
        )
        """,
    ]

    with engine.begin() as connection:
        for statement in schema:
            connection.execute(text(statement))

        connection.execute(
            text(
                """
                INSERT INTO submissions (id, thread_id, status)
                VALUES (1, 'thread-1', 'awaiting_approval')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vendors (id, vendor_name)
                VALUES (1, 'Acme Supplies')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO invoices (
                    id, invoice_number, vendor_id, invoice_date, currency,
                    total, po_number, status, created_at, extracted_data
                )
                VALUES (
                    10, 'INV-010', 1, '2026-11-20', 'INR',
                    1250.50, 'PO-100', 'human_review',
                    '2026-11-20T10:00:00', '{"total": 1250.5}'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO audit_logs (
                    id, invoice_id, action, actor, details, created_at
                )
                VALUES (
                    1, 10, 'invoice_created', 'system',
                    'Invoice imported', '2026-11-20T10:01:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO invoice_review_cases (
                    id, invoice_id, case_type, reason, status, created_at
                )
                VALUES (
                    20, 10, 'unknown_vendor', 'Vendor needs verification',
                    'open', '2026-11-20T10:02:00'
                )
                """
            )
        )

    monkeypatch.setattr(
        api_queries_postgres,
        "get_postgres_engine",
        lambda: engine,
    )
    monkeypatch.setattr(api, "get_database_backend", lambda: "postgresql")

    yield engine
    engine.dispose()


@pytest.fixture
def postgres_client(postgres_query_engine):
    return TestClient(api.app)


def test_postgres_list_submissions(postgres_query_engine):
    rows = api_queries_postgres.list_submissions()

    assert rows == [
        {
            "id": 1,
            "thread_id": "thread-1",
            "status": "awaiting_approval",
        }
    ]


def test_postgres_list_invoices_pagination(postgres_query_engine):
    rows, total = api_queries_postgres.list_invoices(limit=1, offset=0)

    assert total == 1
    assert len(rows) == 1
    assert rows[0]["id"] == 10
    assert rows[0]["vendor_name"] == "Acme Supplies"


def test_postgres_invoice_detail_and_audit_history(postgres_query_engine):
    invoice, logs = api_queries_postgres.get_invoice(10)

    assert invoice["invoice_number"] == "INV-010"
    assert invoice["vendor_name"] == "Acme Supplies"
    assert len(logs) == 1
    assert logs[0]["action"] == "invoice_created"


def test_postgres_invoice_status_lookup(postgres_query_engine):
    assert api_queries_postgres.get_invoice_status(10) == {
        "id": 10,
        "status": "human_review",
    }
    assert api_queries_postgres.get_invoice_status(999) is None


def test_postgres_review_case_queries(postgres_query_engine):
    rows = api_queries_postgres.list_review_cases("open")

    assert len(rows) == 1
    assert rows[0]["id"] == 20
    assert api_queries_postgres.get_review_case(20) == {
        "id": 20,
        "status": "open",
    }
    assert api_queries_postgres.get_review_case(999) is None


def test_postgres_invoice_endpoints(postgres_client, monkeypatch):
    page = postgres_client.get("/invoices?limit=10&offset=0")
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert page.json()["invoices"][0]["vendor_name"] == "Acme Supplies"

    detail = postgres_client.get("/invoices/10")
    assert detail.status_code == 200
    assert detail.json()["invoice"]["extracted_data"] == {"total": 1250.5}
    assert detail.json()["audit_history"][0]["action"] == "invoice_created"

    monkeypatch.setattr(api, "make_decision", lambda *args: True)
    rejected = postgres_client.post(
        "/invoices/10/reject",
        json={"action": "reject", "reason": "Reviewed and rejected"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_postgres_review_case_endpoints(postgres_client, monkeypatch):
    response = postgres_client.get("/review-cases?status=open")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["review_cases"][0]["id"] == 20

    monkeypatch.setattr(api, "reject_review_case", lambda *args: True)
    rejected = postgres_client.post(
        "/review-cases/20/reject",
        json={"action": "reject", "reason": "Investigation completed"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_postgres_workflow_listing_uses_checkpointer_and_submission_queries(
    postgres_query_engine,
    monkeypatch,
):
    checkpoint_rows = [
        SimpleNamespace(
            config={
                "configurable": {
                    "thread_id": "thread-1",
                    "checkpoint_id": "0002",
                }
            }
        ),
        SimpleNamespace(
            config={
                "configurable": {
                    "thread_id": "thread-1",
                    "checkpoint_id": "0001",
                }
            }
        ),
    ]

    class FakeSaver:
        def __init__(self):
            self.called = False

        def list(self, config, *, before=None, limit=None):
            assert config is None

            if self.called:
                return iter(())

            self.called = True
            return iter(checkpoint_rows)

    class FakeGraph:
        def get_state(self, config):
            assert config["configurable"]["thread_id"] == "thread-1"
            return SimpleNamespace(
                values={
                    "invoice_id": 10,
                    "status": "human_review",
                    "review_case_id": 20,
                },
                next=(),
            )

    @contextmanager
    def fake_open_checkpointer():
        yield FakeSaver()

    monkeypatch.setattr(api, "_ledger_ready", lambda: True)
    monkeypatch.setattr(api, "open_checkpointer", fake_open_checkpointer)
    monkeypatch.setattr(api, "get_graph", lambda saver: FakeGraph())

    result = api.list_workflows(limit=10)

    assert result["count"] == 1
    assert result["skipped"] == 0
    assert result["workflows"][0]["thread_id"] == "thread-1"
    assert result["workflows"][0]["submission_id"] == 1
    assert result["workflows"][0]["submission_status"] == "awaiting_approval"
