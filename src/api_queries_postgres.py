"""PostgreSQL-backed read queries used by the FastAPI endpoints.

Writes and business decisions remain in their existing service modules.
This module contains only API read operations and uses SQLAlchemy Core.
"""

from sqlalchemy import text

from src.db_core import get_postgres_engine


def list_submissions():
    """Return submission rows needed by workflow listing and ownership checks."""

    statement = text(
        """
        SELECT id, thread_id, status, submitted_by
        FROM submissions
        WHERE thread_id IS NOT NULL
        """
    )

    with get_postgres_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()

    return [dict(row) for row in rows]


def list_invoices(limit: int, offset: int, user_id=None):
    """Return a page of invoices visible to a user and the matching count."""

    if user_id is None:
        ownership_filter = "TRUE"
        ownership_parameters = {}
    else:
        ownership_filter = """
            EXISTS (
                SELECT 1
                FROM submissions AS s
                WHERE s.thread_id = i.thread_id
                  AND s.submitted_by = :user_id
            )
        """
        ownership_parameters = {"user_id": user_id}

    page_statement = text(
        f"""
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
        WHERE {ownership_filter}
        ORDER BY i.id DESC
        LIMIT :limit OFFSET :offset
        """
    )

    count_statement = text(
        f"""
        SELECT COUNT(*) AS total
        FROM invoices AS i
        WHERE {ownership_filter}
        """
    )

    parameters = {
        "limit": limit,
        "offset": offset,
        **ownership_parameters,
    }

    with get_postgres_engine().connect() as connection:
        rows = connection.execute(
            page_statement,
            parameters,
        ).mappings().all()

        total = connection.execute(
            count_statement,
            ownership_parameters,
        ).scalar_one()

    return [dict(row) for row in rows], total


def get_invoice(invoice_id: int, user_id=None):
    """Return an accessible invoice and its audit history, or (None, [])."""

    if user_id is None:
        ownership_filter = "TRUE"
        parameters = {"invoice_id": invoice_id}
    else:
        ownership_filter = """
            EXISTS (
                SELECT 1
                FROM submissions AS s
                WHERE s.thread_id = i.thread_id
                  AND s.submitted_by = :user_id
            )
        """
        parameters = {
            "invoice_id": invoice_id,
            "user_id": user_id,
        }

    invoice_statement = text(
        f"""
        SELECT i.*, v.vendor_name
        FROM invoices AS i
        LEFT JOIN vendors AS v ON i.vendor_id = v.id
        WHERE i.id = :invoice_id
          AND {ownership_filter}
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
            parameters,
        ).mappings().first()

        if row is None:
            return None, []

        if user_id is not None:
            return dict(row), []

        logs = connection.execute(
            audit_statement,
            {"invoice_id": invoice_id},
        ).mappings().all()

    return dict(row), [dict(log) for log in logs]

def get_invoice_status(invoice_id: int):
    """Return the invoice ID and status, if it exists."""

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
