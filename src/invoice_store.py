
import sqlite3
from datetime import UTC, datetime

from src import db_core


def _postgres():
    return db_core.get_database_backend() == "postgresql"


def _get_postgres_store():
    from src import invoice_store_postgres

    return invoice_store_postgres


def _now():
    return datetime.now(UTC).isoformat()


def has_thread_column(*, conn=None) -> bool:
    if _postgres():
        return _get_postgres_store().has_thread_column(conn=conn)

    """True when invoices.thread_id exists (migration 003 applied)."""
    own = conn is None
    connection = db_core.connect() if own else conn

    try:
        return any(
            row["name"] == "thread_id"
            for row in connection.execute("PRAGMA table_info(invoices)")
        )
    finally:
        if own:
            connection.close()


def find_by_thread(thread_id, *, conn=None):
    if _postgres():
        return _get_postgres_store().find_by_thread(
            thread_id, conn=conn
        )

    """The invoice created by this workflow thread, as a dict, or None."""
    own = conn is None
    connection = db_core.connect() if own else conn

    try:
        row = connection.execute(
            "SELECT * FROM invoices WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        if own:
            connection.close()


def save_invoice_once(invoice, status, thread_id):
    if _postgres():
        return _get_postgres_store().save_invoice_once(
            invoice, status, thread_id
        )

    """
    Save a validated invoice for a workflow thread. Returns (invoice_id, created).

    One BEGIN IMMEDIATE transaction covers the thread lookup, the insert and
    the INVOICE_RECEIVED audit row. Raises ValueError for an unapproved vendor
    or a duplicate vendor and invoice number from another thread, with the same
    messages as src.db.save_invoice.
    """
    connection = db_core.connect()

    try:
        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute(
            "SELECT id FROM invoices WHERE thread_id = ?", (thread_id,)
        ).fetchone()

        if existing is not None:
            connection.rollback()
            return existing["id"], False

        vendor = connection.execute(
            "SELECT id, is_approved FROM vendors WHERE vendor_name = ?",
            (invoice.vendor,),
        ).fetchone()

        if vendor is None:
            raise ValueError(f"Unapproved vendor: {invoice.vendor}")

        if not vendor["is_approved"]:
            raise ValueError(f"Vendor is not approved: {invoice.vendor}")

        now = _now()

        try:
            cursor = connection.execute(
                """
                INSERT INTO invoices (
                    vendor_id, invoice_number, invoice_date, due_date,
                    currency, total, po_number, extracted_data,
                    status, created_at, thread_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vendor["id"],
                    invoice.invoice_number,
                    invoice.invoice_date.isoformat(),
                    invoice.due_date.isoformat() if invoice.due_date else None,
                    invoice.currency,
                    invoice.total,
                    invoice.po_number,
                    invoice.model_dump_json(),
                    status,
                    now,
                    thread_id,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "Duplicate invoice: this vendor and invoice number "
                "have already been recorded."
            ) from error

        invoice_id = cursor.lastrowid

        connection.execute(
            """
            INSERT INTO audit_logs
            (invoice_id, action, actor, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                invoice_id,
                "INVOICE_RECEIVED",
                "SYSTEM",
                "Validated invoice stored in database.",
                now,
            ),
        )

        connection.commit()
        return invoice_id, True

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
