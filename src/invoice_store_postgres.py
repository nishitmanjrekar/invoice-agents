
"""PostgreSQL implementation of idempotent invoice storage."""

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from src.db_core import get_postgres_engine


def _now():
    """Return the current UTC timestamp in ISO format."""

    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def has_thread_column(*, conn=None) -> bool:
    """True when invoices.thread_id exists."""

    own = conn is None
    connection = (
        get_postgres_engine().connect() if own else conn
    )

    try:
        columns = inspect(connection).get_columns("invoices")
        return any(column["name"] == "thread_id" for column in columns)
    finally:
        if own:
            connection.close()


def find_by_thread(thread_id, *, conn=None):
    """Find the invoice created by a workflow thread."""

    own = conn is None
    connection = (
        get_postgres_engine().connect() if own else conn
    )

    try:
        result = connection.execute(
            text("""
                SELECT *
                FROM invoices
                WHERE thread_id = :thread_id
            """),
            {"thread_id": thread_id},
        ).mappings().first()

        return dict(result) if result is not None else None
    finally:
        if own:
            connection.close()


def save_invoice_once(invoice, status, thread_id):
    """
    Save an invoice once per workflow thread.

    Returns (invoice_id, created).

    Replaying the same thread returns its existing invoice without
    changing its status or creating another audit event.
    """

    engine = get_postgres_engine()

    try:
        with engine.begin() as connection:
            existing = connection.execute(
                text("""
                    SELECT id
                    FROM invoices
                    WHERE thread_id = :thread_id
                """),
                {"thread_id": thread_id},
            ).mappings().first()

            if existing is not None:
                return existing["id"], False

            vendor = connection.execute(
                text("""
                    SELECT id, is_approved
                    FROM vendors
                    WHERE vendor_name = :vendor_name
                """),
                {"vendor_name": invoice.vendor},
            ).mappings().first()

            if vendor is None:
                raise ValueError(
                    f"Unapproved vendor: {invoice.vendor}"
                )

            if not vendor["is_approved"]:
                raise ValueError(
                    f"Vendor is not approved: {invoice.vendor}"
                )

            now = _now()

            result = connection.execute(
                text("""
                    INSERT INTO invoices (
                        vendor_id,
                        invoice_number,
                        invoice_date,
                        due_date,
                        currency,
                        total,
                        po_number,
                        extracted_data,
                        status,
                        created_at,
                        thread_id
                    )
                    VALUES (
                        :vendor_id,
                        :invoice_number,
                        :invoice_date,
                        :due_date,
                        :currency,
                        :total,
                        :po_number,
                        :extracted_data,
                        :status,
                        :created_at,
                        :thread_id
                    )
                    ON CONFLICT (thread_id)
                    WHERE thread_id IS NOT NULL
                    DO NOTHING
                    RETURNING id
                """),
                {
                    "vendor_id": vendor["id"],
                    "invoice_number": invoice.invoice_number,
                    "invoice_date": invoice.invoice_date.isoformat(),
                    "due_date": (
                        invoice.due_date.isoformat()
                        if invoice.due_date else None
                    ),
                    "currency": invoice.currency,
                    "total": invoice.total,
                    "po_number": invoice.po_number,
                    "extracted_data": invoice.model_dump_json(),
                    "status": status,
                    "created_at": now,
                    "thread_id": thread_id,
                },
            )

            invoice_id = result.scalar_one_or_none()

            if invoice_id is None:
                existing = connection.execute(
                    text("""
                        SELECT id
                        FROM invoices
                        WHERE thread_id = :thread_id
                    """),
                    {"thread_id": thread_id},
                ).mappings().first()

                if existing is None:
                    raise RuntimeError(
                        "Invoice thread conflict occurred, "
                        "but the existing invoice could not be found."
                    )

                return existing["id"], False

            connection.execute(
                text("""
                    INSERT INTO audit_logs (
                        invoice_id,
                        action,
                        actor,
                        details,
                        created_at
                    )
                    VALUES (
                        :invoice_id,
                        :action,
                        :actor,
                        :details,
                        :created_at
                    )
                """),
                {
                    "invoice_id": invoice_id,
                    "action": "INVOICE_RECEIVED",
                    "actor": "SYSTEM",
                    "details": (
                        "Validated invoice stored in database."
                    ),
                    "created_at": now,
                },
            )

            return invoice_id, True

    except IntegrityError as error:
        raise ValueError(
            "Duplicate invoice: this vendor and invoice number "
            "have already been recorded."
        ) from error
