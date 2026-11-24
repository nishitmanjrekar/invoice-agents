
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from src.db_core import (
    DB_PATH,
    PROJECT_ROOT,
    connect,
    get_database_backend,
)

DATA_DIR = PROJECT_ROOT / "data"


# Approved vendors from the synthetic dataset.
APPROVED_VENDORS = [
    "Acme Supplies",
    "Global Office Solutions",
    "TechNova Systems",
    "Bright Electronics",
    "Prime Industrial",
    "Reliable Logistics",
    "Vertex Technologies",
    "National Stationers",
]

def _postgres():
    """Return whether PostgreSQL is the configured backend."""

    return get_database_backend() == "postgresql"

def _get_postgres_db():
    """Load PostgreSQL operations only when PostgreSQL is selected."""

    from src import db_postgres

    return db_postgres

def current_timestamp():
    return datetime.now(UTC).isoformat()


@contextmanager
def db_connection():
    """Open a database connection and handle commit/rollback."""

    connection = connect()

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_db():
    """Create all database tables without deleting existing records."""

    if _postgres():
        return _get_postgres_db().initialize_db()

    with db_connection() as conn:

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_name TEXT NOT NULL UNIQUE,
                is_approved INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS purchase_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id INTEGER NOT NULL,
                po_number TEXT NOT NULL,
                po_amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'INR',
                created_at TEXT NOT NULL,

                FOREIGN KEY (vendor_id)
                    REFERENCES vendors(id),

                UNIQUE (vendor_id, po_number)
            );

            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id INTEGER NOT NULL,
                invoice_number TEXT NOT NULL,
                invoice_date TEXT NOT NULL,
                due_date TEXT,
                currency TEXT NOT NULL,
                total REAL NOT NULL,
                po_number TEXT,
                extracted_data TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'received',
                created_at TEXT NOT NULL,

                FOREIGN KEY (vendor_id)
                    REFERENCES vendors(id),

                UNIQUE (vendor_id, invoice_number)
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL,

                FOREIGN KEY (invoice_id)
                    REFERENCES invoices(id)
            );

            CREATE TABLE IF NOT EXISTS invoice_review_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL,
                vendor_name TEXT NOT NULL,
                case_type TEXT NOT NULL,
                invoice_data TEXT NOT NULL,
                validation_report TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                existing_invoice_id INTEGER,
                status TEXT NOT NULL DEFAULT 'open',
                reviewer_reason TEXT,
                created_at TEXT NOT NULL,
                reviewed_at TEXT,

                FOREIGN KEY (existing_invoice_id)
                    REFERENCES invoices(id),

                CHECK (
                    case_type IN (
                        'unknown_vendor',
                        'duplicate'
                    )
                ),

                CHECK (
                    status IN (
                        'open',
                        'rejected',
                        'resolved'
                    )
                )
            );

            CREATE INDEX IF NOT EXISTS idx_invoice_number
                ON invoices(invoice_number);

            CREATE INDEX IF NOT EXISTS idx_invoice_status
                ON invoices(status);

            CREATE INDEX IF NOT EXISTS idx_po_number
                ON purchase_orders(po_number);

            CREATE INDEX IF NOT EXISTS idx_review_case_status
                ON invoice_review_cases(status);
        """)


def seed_vendors():
    """Insert approved vendors into the database."""

    if _postgres():
        _get_postgres_db().seed_vendors()
        return

    with db_connection() as conn:

        for vendor in APPROVED_VENDORS:
            conn.execute(
                """
                INSERT OR IGNORE INTO vendors
                (vendor_name, is_approved, created_at)
                VALUES (?, 1, ?)
                """,
                (vendor, current_timestamp())
            )


def seed_purchase_orders():
    """
    Load simulated purchase-order reference data.

    Only approved vendors are imported.
    Invoice issue labels are never imported or used.
    """
    if _postgres():
        return _get_postgres_db().seed_purchase_orders()

    if not DATA_DIR.exists():
        print("WARNING: Data directory not found.")
        return

    imported = 0

    with db_connection() as conn:

        for json_file in sorted(DATA_DIR.glob("inv_*.json")):

            try:
                with open(json_file, encoding="utf-8") as file:
                    data = json.load(file)

                vendor_name = data.get("vendor")
                po_number = data.get("po_number")
                po_amount = data.get("po_amount")

                if not vendor_name or not po_number:
                    continue

                vendor = conn.execute(
                    """
                    SELECT id FROM vendors
                    WHERE vendor_name = ?
                    AND is_approved = 1
                    """,
                    (vendor_name,)
                ).fetchone()

                if not vendor:
                    continue

                if po_amount is None:
                    continue

                try:
                    po_amount = float(po_amount)
                except (TypeError, ValueError):
                    continue

                if po_amount <= 0:
                    continue

                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO purchase_orders
                    (vendor_id, po_number, po_amount, currency, created_at)
                    VALUES (?, ?, ?, 'INR', ?)
                    """,
                    (
                        vendor["id"],
                        po_number,
                        po_amount,
                        current_timestamp()
                    )
                )

                imported += cursor.rowcount

            except (json.JSONDecodeError, OSError) as error:
                print(f"Skipping {json_file.name}: {error}")

    print(f"Purchase orders imported: {imported}")


def get_vendor(vendor_name):
    """Find a vendor by name."""

    if _postgres():
        return _get_postgres_db().get_vendor(vendor_name)

    with db_connection() as conn:

        result = conn.execute(
            """
            SELECT * FROM vendors
            WHERE vendor_name = ?
            """,
            (vendor_name,)
        ).fetchone()

        return dict(result) if result else None


def get_purchase_order(vendor_name, po_number):
    """Find a purchase order belonging to a specific vendor."""

    if _postgres():
        return _get_postgres_db().get_purchase_order(
            vendor_name, po_number
        )

    with db_connection() as conn:

        result = conn.execute(
            """
            SELECT
                po.id,
                po.po_number,
                po.po_amount,
                po.currency,
                v.vendor_name
            FROM purchase_orders po
            JOIN vendors v ON po.vendor_id = v.id
            WHERE v.vendor_name = ?
            AND po.po_number = ?
            """,
            (vendor_name, po_number)
        ).fetchone()

        return dict(result) if result else None


def find_duplicate(vendor_name, invoice_number):
    """Check whether an invoice has already been recorded."""

    if _postgres():
        return _get_postgres_db().find_duplicate(
            vendor_name, invoice_number
        )

    with db_connection() as conn:

        result = conn.execute(
            """
            SELECT id, vendor_id, invoice_number, status, created_at
            FROM invoices
            WHERE vendor_id = (
                SELECT id FROM vendors
                WHERE vendor_name = ?
            )
            AND invoice_number = ?
            """,
            (vendor_name, invoice_number)
        ).fetchone()

        return dict(result) if result else None


def save_invoice(invoice, status="received"):
    """Save a validated invoice and prevent duplicate records."""

    if _postgres():
        return _get_postgres_db().save_invoice(invoice, status)

    vendor = get_vendor(invoice.vendor)

    if not vendor:
        raise ValueError(
            f"Unapproved vendor: {invoice.vendor}"
        )

    if not vendor["is_approved"]:
        raise ValueError(
            f"Vendor is not approved: {invoice.vendor}"
        )

    extracted_data = invoice.model_dump_json()

    with db_connection() as conn:

        try:
            cursor = conn.execute(
                """
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
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vendor["id"],
                    invoice.invoice_number,
                    invoice.invoice_date.isoformat(),
                    (
                        invoice.due_date.isoformat()
                        if invoice.due_date else None
                    ),
                    invoice.currency,
                    invoice.total,
                    invoice.po_number,
                    extracted_data,
                    status,
                    current_timestamp()
                )
            )

            invoice_id = cursor.lastrowid

            conn.execute(
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
                    current_timestamp()
                )
            )

            return invoice_id

        except sqlite3.IntegrityError as error:
            raise ValueError(
                "Duplicate invoice: this vendor and invoice number "
                "have already been recorded."
            ) from error


def create_review_case(invoice, validation_report, case_type):

    if _postgres():
        return _get_postgres_db().create_review_case(
            invoice, validation_report, case_type
        )

    if case_type not in ("unknown_vendor", "duplicate"):
        raise ValueError(
            "Invalid review case type."
        )

    duplicate_check = validation_report["checks"]["duplicate"]

    existing_invoice_id = (
        duplicate_check.get("existing_invoice_id")
        if case_type == "duplicate"
        else None
    )

    timestamp = current_timestamp()

    with db_connection() as conn:

        cursor = conn.execute(
            """
            INSERT INTO invoice_review_cases (
                invoice_number,
                vendor_name,
                case_type,
                invoice_data,
                validation_report,
                risk_level,
                existing_invoice_id,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                invoice.invoice_number,
                invoice.vendor,
                case_type,
                invoice.model_dump_json(),
                json.dumps(validation_report),
                validation_report["risk_level"],
                existing_invoice_id,
                timestamp,
            ),
        )

        case_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO audit_logs (
                invoice_id,
                action,
                actor,
                details,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                existing_invoice_id,
                "INVOICE_REVIEW_CASE_CREATED",
                "SYSTEM",
                json.dumps({
                    "case_id": case_id,
                    "invoice_number": invoice.invoice_number,
                    "vendor": invoice.vendor,
                    "case_type": case_type,
                    "risk_level": validation_report["risk_level"],
                }),
                timestamp,
            ),
        )

        return case_id


def log_audit_event(invoice_id, action, actor, details):
    """Record a system or human action in the audit log."""

    if _postgres():
        return _get_postgres_db().log_audit_event(
            invoice_id, action, actor, details
        )

    with db_connection() as conn:

        conn.execute(
            """
            INSERT INTO audit_logs
            (invoice_id, action, actor, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                invoice_id,
                action,
                actor,
                details,
                current_timestamp()
            )
        )


def get_database_summary():
    """Return counts of all stored records."""

    if _postgres():
        return _get_postgres_db().get_database_summary()

    with db_connection() as conn:

        summary = {}

        for table in [
            "vendors",
            "purchase_orders",
            "invoices",
            "audit_logs",
            "invoice_review_cases",
        ]:
            result = conn.execute(
                f"SELECT COUNT(*) AS total FROM {table}"
            ).fetchone()

            summary[table] = result["total"]

        return summary


if __name__ == "__main__":

    print("\nINITIALIZING INVOICE DATABASE")
    print("=" * 45)

    initialize_db()
    print("Database tables created successfully.")

    seed_vendors()
    print("Approved vendors seeded successfully.")

    seed_purchase_orders()

    summary = get_database_summary()

    print("\nDATABASE SUMMARY")
    print("=" * 45)

    for table, count in summary.items():
        print(f"{table.replace('_', ' ').title():24}: {count}")

    print("=" * 45)
    print(f"Database location: {DB_PATH}")
    print("DATABASE INITIALIZATION SUCCESSFUL")
