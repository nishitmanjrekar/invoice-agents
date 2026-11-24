
import json

from mcp.server import MCPServer

from src.db import (
    db_connection,
    find_duplicate,
    get_purchase_order,
    get_vendor,
)
from src.db import (
    get_database_summary as fetch_database_summary,
)

mcp = MCPServer(
    "Invoice Processing MCP Server",
    instructions=(
        "Provides read-only access to invoice processing data. "
        "Never approve, reject, or initiate payments."
    ),
)


def to_json(data):
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
def get_vendor_details(vendor_name: str) -> str:
    """Retrieve vendor details and approval status."""

    if not vendor_name or not vendor_name.strip():
        return to_json({"error": "Vendor name is required."})

    vendor = get_vendor(vendor_name.strip())

    if not vendor:
        return to_json({
            "found": False,
            "message": "Vendor not found.",
        })

    return to_json({
        "found": True,
        "vendor": vendor,
    })


@mcp.tool()
def get_purchase_order_details(
    vendor_name: str,
    po_number: str,
) -> str:
    """Retrieve a purchase order belonging to a specific vendor."""

    if not vendor_name or not vendor_name.strip():
        return to_json({"error": "Vendor name is required."})

    if not po_number or not po_number.strip():
        return to_json({"error": "PO number is required."})

    purchase_order = get_purchase_order(
        vendor_name.strip(),
        po_number.strip(),
    )

    if not purchase_order:
        return to_json({
            "found": False,
            "message": "Purchase order not found for this vendor.",
        })

    return to_json({
        "found": True,
        "purchase_order": purchase_order,
    })


@mcp.tool()
def check_duplicate_invoice(
    vendor_name: str,
    invoice_number: str,
) -> str:
    """Check whether an invoice already exists for a vendor."""

    if not vendor_name or not vendor_name.strip():
        return to_json({"error": "Vendor name is required."})

    if not invoice_number or not invoice_number.strip():
        return to_json({"error": "Invoice number is required."})

    duplicate = find_duplicate(
        vendor_name.strip(),
        invoice_number.strip(),
    )

    return to_json({
        "is_duplicate": duplicate is not None,
        "existing_invoice": duplicate,
    })


@mcp.tool()
def get_database_summary() -> str:
    """Retrieve counts of all invoice database records."""

    return to_json({
        "database_summary": fetch_database_summary(),
    })


@mcp.tool()
def list_review_cases(limit: int = 20) -> str:
    """List open human-review cases without modifying them."""

    if limit < 1 or limit > 100:
        return to_json({
            "error": "Limit must be between 1 and 100."
        })

    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                invoice_number,
                vendor_name,
                case_type,
                risk_level,
                existing_invoice_id,
                status,
                created_at
            FROM invoice_review_cases
            WHERE status = 'open'
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    cases = [dict(row) for row in rows]

    return to_json({
        "count": len(cases),
        "review_cases": cases,
    })


if __name__ == "__main__":
    mcp.run()
