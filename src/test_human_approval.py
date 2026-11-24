
import tempfile
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src.agents.invoice_graph import build_invoice_graph
from src.test_graph_categories import build_offline_invoice

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INVOICE_PATH = PROJECT_ROOT / "data" / "inv_001.txt"


def main():
    print("LANGGRAPH HUMAN APPROVAL INTEGRATION TEST")
    print("=" * 60)
    print("Mode: Offline")
    print("Gemini API calls: None")
    print("Real invoice database writes: None")

    if not INVOICE_PATH.exists():
        raise FileNotFoundError(INVOICE_PATH)

    INVOICE_PATH.read_text(encoding="utf-8")

    def offline_extract(text):
        return build_offline_invoice(text, "inv_001")

    with tempfile.TemporaryDirectory() as temp_dir:
        checkpoint_path = Path(temp_dir) / "test_checkpoints.sqlite"

        with (
            patch("src.extract.extract_invoice", side_effect=offline_extract),
            patch(
                "src.rules.get_vendor",
                return_value={
                    "vendor_name": "Global Office Solutions",
                    "is_approved": True,
                },
            ),
            patch("src.rules.find_duplicate", return_value=None),
            patch(
                "src.db.get_vendor",
                return_value={
                    "vendor_name": "Global Office Solutions",
                    "is_approved": True,
                },
            ),
            patch("src.db.find_duplicate", return_value=None),
            patch("src.db.save_invoice", return_value=4321),
            patch("src.review.make_decision", return_value=True) as decision_mock,
        ):
            with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
                saver.setup()
                saver.serde = JsonPlusSerializer(
                    allowed_msgpack_modules=[
                        ("src.schemas", "Invoice")
                    ]
                )

                graph = build_invoice_graph(
                    checkpointer=saver,
                    human_in_the_loop=True,
                )

                config = {
                    "configurable": {
                        "thread_id": "human-approval-integration-001"
                    }
                }

                print("\nTEST 1: Start clean invoice workflow")

                first_result = graph.invoke(
                    {"invoice_path": str(INVOICE_PATH)},
                    config,
                )

                assert first_result["status"] == "pending_approval"
                assert first_result["invoice_id"] == 4321
                assert first_result.get("__interrupt__"), (
                    "Workflow did not pause for human approval"
                )

                print("Invoice saved with pending_approval status: PASS")
                print("Invoice ID:", first_result["invoice_id"])

                print("\nTEST 2: Verify saved checkpoint")

                saved_state = graph.get_state(config)

                assert saved_state.values["status"] == "pending_approval"
                assert "human_approval" in saved_state.next

                print("Checkpoint contains invoice state: PASS")
                print("Pending node:", saved_state.next)

                print("\nTEST 3: Resume with human approval")

                final_result = graph.invoke(
                    Command(
                        resume={
                            "action": "approve",
                            "reason": "",
                        }
                    ),
                    config,
                )

                assert final_result["status"] == "approved"
                assert final_result["reviewer_decision"] == "approve"

                decision_mock.assert_called_once_with(
                    invoice_id=4321,
                    decision="approve",
                    reason="",
                )

                print("Human approval recorded: PASS")
                print("Final status:", final_result["status"])

                print("\nTEST 4: Verify workflow completion")

                completed_state = graph.get_state(config)

                assert not completed_state.next

                print("Workflow completed: PASS")

    print("\n" + "=" * 60)
    print("ALL HUMAN APPROVAL INTEGRATION TESTS PASSED")
    print("Temporary checkpoint database removed.")
    print("No Gemini calls or real invoice database writes.")


if __name__ == "__main__":
    main()
