
import asyncio
import json

from mcp import Client

from src.mcp_servers.invoice_server import mcp


async def main():
    print("MCP SERVER INTEGRATION TEST")
    print("=" * 45)

    async with Client(mcp) as client:

        # Test 1: Discover tools
        result = await client.list_tools()

        tool_names = {tool.name for tool in result.tools}

        expected_tools = {
            "get_vendor_details",
            "get_purchase_order_details",
            "check_duplicate_invoice",
            "get_database_summary",
            "list_review_cases",
        }

        assert expected_tools.issubset(tool_names), (
            f"Missing tools: {expected_tools - tool_names}"
        )

        print("TEST 1: All five MCP tools discovered - PASS")

        # Test 2: Vendor lookup
        result = await client.call_tool(
            "get_vendor_details",
            {"vendor_name": "Global Office Solutions"},
        )

        vendor_data = json.loads(result.content[0].text)
        assert vendor_data["found"] is True

        print("TEST 2: Vendor lookup - PASS")

        # Test 3: Purchase order lookup
        result = await client.call_tool(
            "get_purchase_order_details",
            {
                "vendor_name": "Global Office Solutions",
                "po_number": "PO-2001",
            },
        )

        json.loads(result.content[0].text)
        print("TEST 3: Purchase order lookup - PASS")

        # Test 4: Duplicate invoice lookup
        result = await client.call_tool(
            "check_duplicate_invoice",
            {
                "vendor_name": "Global Office Solutions",
                "invoice_number": "MCP-TEST-NOT-REAL",
            },
        )

        duplicate_data = json.loads(result.content[0].text)
        assert duplicate_data["is_duplicate"] is False

        print("TEST 4: Duplicate invoice lookup - PASS")

        # Test 5: Database summary
        result = await client.call_tool(
            "get_database_summary",
            {},
        )

        summary_data = json.loads(result.content[0].text)
        assert "database_summary" in summary_data

        print("TEST 5: Database summary - PASS")

        # Test 6: Review cases
        result = await client.call_tool(
            "list_review_cases",
            {"limit": 20},
        )

        review_data = json.loads(result.content[0].text)
        assert "review_cases" in review_data

        print("TEST 6: Review case listing - PASS")

    print("=" * 45)
    print("ALL MCP INTEGRATION TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
