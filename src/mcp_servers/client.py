
import json

from mcp import Client

from src.mcp_servers.invoice_server import mcp


async def call_invoice_tool(tool_name: str, arguments: dict) -> dict:
    """
    Call a tool exposed by the invoice MCP server.

    This uses an in-process MCP client, so no separate server
    process or network connection is required.
    """

    async with Client(mcp) as client:
        result = await client.call_tool(
            tool_name,
            arguments,
        )

    if result.is_error:
        raise RuntimeError(
            f"MCP tool '{tool_name}' returned an error."
        )

    if not result.content:
        raise RuntimeError(
            f"MCP tool '{tool_name}' returned no content."
        )

    response_text = result.content[0].text

    try:
        return json.loads(response_text)
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError(
            f"MCP tool '{tool_name}' returned invalid JSON."
        ) from error


async def verify_invoice_with_mcp(
    vendor_name: str,
    invoice_number: str,
) -> dict:
    """Retrieve vendor and duplicate information through MCP."""

    vendor_result = await call_invoice_tool(
        "get_vendor_details",
        {"vendor_name": vendor_name},
    )

    duplicate_result = await call_invoice_tool(
        "check_duplicate_invoice",
        {
            "vendor_name": vendor_name,
            "invoice_number": invoice_number,
        },
    )

    vendor = vendor_result.get("vendor")

    return {
        "vendor_found": vendor_result.get("found", False),
        "vendor_approved": bool(
            vendor and vendor.get("is_approved")
        ),
        "is_duplicate": duplicate_result.get(
            "is_duplicate",
            False,
        ),
    }
