"""PostgreSQL-backed read queries used by the FastAPI endpoints.

Writes and business decisions remain in their existing service modules.
This module contains only API read operations and uses SQLAlchemy Core.
"""

from sqlalchemy import text

from src.db_core import get_postgres_engine


def list_submissions():
    """Return the submission ledger rows needed by the workflow listing."""

    statement = text(
        """
        SELECT id, thread_id, status
        FROM submissions
        WHERE thread_id IS NOT NULL
        """
    )

    with get_postgres_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()

    return [dict(row) for row in rows]


def list_invoices(limit: int, offset: int):
    """Return a page of invoices and the total invoice count."""

    page_statement = text(
        """
        SELECT
            i.id,
            i.invoice_number,
            v.vendor_name,
            i.invoice_date,
            i.currency,
            i.total,
            i.po_number,
            i.status,
            i.created_at
        FROM invoices AS i
        LEFT JOIN vendors AS v ON i.vendor_id = v.id
        ORDER BY i.id DESC
        LIMIT :limit OFFSET :offset
        """
    )
    count_statement = text("SELECT COUNT(*) AS total FROM invoices")

    with get_postgres_engine().connect() as connection:
        rows = connection.execute(
            page_statement,
            {"limit": limit, "offset": offset},
        ).mappings().all()
        total = connection.execute(count_statement).scalar_one()

    return [dict(row) for row in rows], total


def get_invoice(invoice_id: int):
    """Return one invoice and its audit history, or (None, [])."""

    invoice_statement = text(
        """
        SELECT i.*, v.vendor_name
        FROM invoices AS i
        LEFT JOIN vendors AS v ON i.vendor_id = v.id
        WHERE i.id = :invoice_id
        """
    )
    audit_statement = text(
        """
        SELECT action, actor, details, created_at
        FROM audit_logs
        WHERE invoice_id = :invoice_id
        ORDER BY id
        """
    )

    with get_postgres_engine().connect() as connection:
        row = connection.execute(
            invoice_statement,
            {"invoice_id": invoice_id},
        ).mappings().first()

        if row is None:
            return None, []

        logs = connection.execute(
            audit_statement,
            {"invoice_id": invoice_id},
        ).mappings().all()

    return dict(row), [dict(log) for log in logs]


def get_invoice_status(invoice_id: int):
    """Return the ID and status for an invoice, if it exists."""

    statement = text(
        """
        SELECT id, status
        FROM invoices
        WHERE id = :invoice_id
        """
    )

    with get_postgres_engine().connect() as connection:
        row = connection.execute(
            statement,
            {"invoice_id": invoice_id},
        ).mappings().first()

    return dict(row) if row is not None else None


def list_review_cases(status: str):
    """Return review cases, optionally including every status."""

    if status == "all":
        statement = text(
            """
            SELECT *
            FROM invoice_review_cases
            ORDER BY id DESC
            """
        )
        parameters = {}
    else:
        statement = text(
            """
            SELECT *
            FROM invoice_review_cases
            WHERE status = :status
            ORDER BY id DESC
            """
        )
        parameters = {"status": status}

    with get_postgres_engine().connect() as connection:
        rows = connection.execute(
            statement,
            parameters,
        ).mappings().all()

    return [dict(row) for row in rows]


def get_review_case(case_id: int):
    """Return the review case ID and status, if it exists."""

    statement = text(
        """
        SELECT id, status
        FROM invoice_review_cases
        WHERE id = :case_id
        """
    )

    with get_postgres_engine().connect() as connection:
        row = connection.execute(
            statement,
            {"case_id": case_id},
        ).mappings().first()

    return dict(row) if row is not None else None
