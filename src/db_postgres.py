
"""PostgreSQL database operations using SQLAlchemy Core."""
import json
from contextlib import contextmanager

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from src.db_core import PROJECT_ROOT, get_postgres_engine


def current_timestamp():
    """Return the current UTC timestamp in ISO format."""

    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


@contextmanager
def db_connection():
    """Open a PostgreSQL connection with transaction management."""

    engine = get_postgres_engine()

    with engine.begin() as connection:
        yield connection


def get_vendor(vendor_name):
    """Find a vendor by name."""

    with db_connection() as conn:
        result = conn.execute(
            text("""
                SELECT *
                FROM vendors
                WHERE vendor_name = :vendor_name
            """),
            {"vendor_name": vendor_name},
        ).mappings().first()

        return dict(result) if result else None


def get_purchase_order(vendor_name, po_number):
    """Find a purchase order belonging to a specific vendor."""

    with db_connection() as conn:
        result = conn.execute(
            text("""
                SELECT
                    po.id,
                    po.po_number,
                    po.po_amount,
                    po.currency,
                    v.vendor_name
                FROM purchase_orders po
                JOIN vendors v ON po.vendor_id = v.id
                WHERE v.vendor_name = :vendor_name
                AND po.po_number = :po_number
            """),
            {
                "vendor_name": vendor_name,
                "po_number": po_number,
            },
        ).mappings().first()

        return dict(result) if result else None


def find_duplicate(vendor_name, invoice_number):
    """Check whether an invoice has already been recorded."""

    with db_connection() as conn:
        result = conn.execute(
            text("""
                SELECT
                    id,
                    vendor_id,
                    invoice_number,
                    status,
                    created_at
                FROM invoices
                WHERE vendor_id = (
                    SELECT id
                    FROM vendors
                    WHERE vendor_name = :vendor_name
                )
                AND invoice_number = :invoice_number
            """),
            {
                "vendor_name": vendor_name,
                "invoice_number": invoice_number,
            },
        ).mappings().first()

        return dict(result) if result else None


DATA_DIR = PROJECT_ROOT / "data"

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


def initialize_db():
    """Verify that PostgreSQL migrations have created the application schema."""

    engine = get_postgres_engine()

    required_tables = {
        "vendors",
        "purchase_orders",
        "invoices",
        "audit_logs",
        "invoice_review_cases",
        "submissions",
        "llm_calls",
        "users",
    }

    existing_tables = set(inspect(engine).get_table_names())
    missing_tables = required_tables - existing_tables

    if missing_tables:
        raise RuntimeError(
            "PostgreSQL schema is incomplete. Missing tables: "
            + ", ".join(sorted(missing_tables))
            + ". Run python -m src.migrate_postgres upgrade first."
        )



def seed_vendors():
    """Insert approved vendors into the database."""

    with db_connection() as conn:
        for vendor in APPROVED_VENDORS:
            conn.execute(
                text("""
                    INSERT INTO vendors (
                        vendor_name,
                        is_approved,
                        created_at
                    )
                    VALUES (
                        :vendor_name,
                        TRUE,
                        :created_at
                    )
                    ON CONFLICT (vendor_name) DO NOTHING
                """),
                {
                    "vendor_name": vendor,
                    "created_at": current_timestamp(),
                },
            )




def seed_purchase_orders():
    """Load simulated purchase-order reference data."""

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

                if po_amount is None:
                    continue

                try:
                    po_amount = float(po_amount)
                except (TypeError, ValueError):
                    continue

                if po_amount <= 0:
                    continue

                vendor = conn.execute(
                    text("""
                        SELECT id
                        FROM vendors
                        WHERE vendor_name = :vendor_name
                        AND is_approved = TRUE
                    """),
                    {"vendor_name": vendor_name},
                ).mappings().first()

                if not vendor:
                    continue

                result = conn.execute(
                    text("""
                        INSERT INTO purchase_orders (
                            vendor_id,
                            po_number,
                            po_amount,
                            currency,
                            created_at
                        )
                        VALUES (
                            :vendor_id,
                            :po_number,
                            :po_amount,
                            'INR',
                            :created_at
                        )
                        ON CONFLICT (vendor_id, po_number) DO NOTHING
                    """),
                    {
                        "vendor_id": vendor["id"],
                        "po_number": po_number,
                        "po_amount": po_amount,
                        "created_at": current_timestamp(),
                    },
                )

                imported += result.rowcount or 0

            except (json.JSONDecodeError, OSError) as error:
                print(f"Skipping {json_file.name}: {error}")

    print(f"Purchase orders imported: {imported}")



def save_invoice(invoice, status="received"):
    """Save a validated invoice and prevent duplicate records."""

    vendor = get_vendor(invoice.vendor)

    if not vendor:
        raise ValueError(f"Unapproved vendor: {invoice.vendor}")

    if not vendor["is_approved"]:
        raise ValueError(
            f"Vendor is not approved: {invoice.vendor}"
        )

    extracted_data = invoice.model_dump_json()

    try:
        with db_connection() as conn:
            result = conn.execute(
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
                        created_at
                    )
                    VALUES (
                        :vendor_id, :invoice_number, :invoice_date,
                        :due_date, :currency, :total, :po_number,
                        :extracted_data, :status, :created_at
                    )
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
                    "extracted_data": extracted_data,
                    "status": status,
                    "created_at": current_timestamp(),
                },
            )

            invoice_id = result.scalar_one()

            conn.execute(
                text("""
                    INSERT INTO audit_logs (
                        invoice_id, action, actor, details, created_at
                    )
                    VALUES (
                        :invoice_id, :action, :actor, :details, :created_at
                    )
                """),
                {
                    "invoice_id": invoice_id,
                    "action": "INVOICE_RECEIVED",
                    "actor": "SYSTEM",
                    "details": "Validated invoice stored in database.",
                    "created_at": current_timestamp(),
                },
            )

            return invoice_id

    except IntegrityError as error:
        raise ValueError(
            "Duplicate invoice: this vendor and invoice number "
            "have already been recorded."
        ) from error




def create_review_case(invoice, validation_report, case_type):
    """Store a submission as a separate review case."""

    if case_type not in ("unknown_vendor", "duplicate"):
        raise ValueError("Invalid review case type.")

    duplicate_check = validation_report["checks"]["duplicate"]

    existing_invoice_id = (
        duplicate_check.get("existing_invoice_id")
        if case_type == "duplicate"
        else None
    )

    timestamp = current_timestamp()

    with db_connection() as conn:
        result = conn.execute(
            text("""
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
                VALUES (
                    :invoice_number, :vendor_name, :case_type,
                    :invoice_data, :validation_report, :risk_level,
                    :existing_invoice_id, 'open', :created_at
                )
                RETURNING id
            """),
            {
                "invoice_number": invoice.invoice_number,
                "vendor_name": invoice.vendor,
                "case_type": case_type,
                "invoice_data": invoice.model_dump_json(),
                "validation_report": json.dumps(validation_report),
                "risk_level": validation_report["risk_level"],
                "existing_invoice_id": existing_invoice_id,
                "created_at": timestamp,
            },
        )

        case_id = result.scalar_one()

        conn.execute(
            text("""
                INSERT INTO audit_logs (
                    invoice_id, action, actor, details, created_at
                )
                VALUES (
                    :invoice_id, :action, :actor, :details, :created_at
                )
            """),
            {
                "invoice_id": existing_invoice_id,
                "action": "INVOICE_REVIEW_CASE_CREATED",
                "actor": "SYSTEM",
                "details": json.dumps({
                    "case_id": case_id,
                    "invoice_number": invoice.invoice_number,
                    "vendor": invoice.vendor,
                    "case_type": case_type,
                    "risk_level": validation_report["risk_level"],
                }),
                "created_at": timestamp,
            },
        )

        return case_id



def log_audit_event(invoice_id, action, actor, details):
    """Record an audit event."""

    with db_connection() as conn:
        conn.execute(
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
                "action": action,
                "actor": actor,
                "details": details,
                "created_at": current_timestamp(),
            },
        )


def get_database_summary():
    """Return row counts for the main application tables."""

    tables = (
        "vendors",
        "purchase_orders",
        "invoices",
        "audit_logs",
        "invoice_review_cases",
    )

    summary = {}

    with db_connection() as conn:
        for table in tables:
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            )
            summary[table] = result.scalar_one()

    return summary


def check_postgres_connection():
    """Check PostgreSQL connectivity and verify the application schema."""

    engine = get_postgres_engine()

    with engine.connect() as conn:
        server_version = conn.execute(
            text("SELECT version()")
        ).scalar_one()

    initialize_db()

    return {
        "connected": True,
        "server_version": server_version,
        "summary": get_database_summary(),
    }
