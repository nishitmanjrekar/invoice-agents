
import argparse
import uuid
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src.agents.invoice_graph import build_invoice_graph

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_DB = PROJECT_ROOT / "checkpoints.sqlite"


def get_graph(checkpointer):
    return build_invoice_graph(
        checkpointer=checkpointer,
        human_in_the_loop=True,
        use_mcp=True,
    )


def start_invoice(invoice_path):
    thread_id = str(uuid.uuid4())

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    print("\nSTARTING INVOICE WORKFLOW")
    print("=" * 50)
    print("Thread ID:", thread_id)
    print("Invoice:", invoice_path)

    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        saver.setup()

        saver.serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("src.schemas", "Invoice")
            ]
        )

        graph = get_graph(saver)

        result = graph.invoke(
            {"invoice_path": invoice_path},
            config,
        )

        print("\nWORKFLOW RESULT")
        print("=" * 50)
        print("Status:", result.get("status"))
        print("Invoice ID:", result.get("invoice_id"))

        interruptions = result.get("__interrupt__", [])

        if interruptions:
            for interruption in interruptions:
                print("\nHUMAN APPROVAL REQUIRED")
                print(interruption.value)

            print("\nWorkflow checkpoint saved.")
            print("Use the Thread ID above to resume this invoice.")

        else:
            print("Workflow completed without an approval pause.")


def resume_invoice(thread_id, action, reason):
    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    if action not in ("approve", "reject"):
        raise ValueError("Action must be approve or reject")

    if action == "reject" and not reason.strip():
        raise ValueError("A rejection reason is required")

    print("\nRESUMING INVOICE WORKFLOW")
    print("=" * 50)
    print("Thread ID:", thread_id)
    print("Decision:", action)

    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        saver.setup()

        saver.serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("src.schemas", "Invoice")
            ]
        )

        graph = get_graph(saver)

        saved_state = graph.get_state(config)

        if not saved_state.values:
            raise ValueError("No saved workflow found for this Thread ID")

        if not saved_state.next:
            raise ValueError("This workflow is already completed")

        result = graph.invoke(
            Command(
                resume={
                    "action": action,
                    "reason": reason,
                }
            ),
            config,
        )

        print("\nFINAL WORKFLOW RESULT")
        print("=" * 50)
        print("Status:", result.get("status"))
        print("Invoice ID:", result.get("invoice_id"))
        print("Decision:", result.get("reviewer_decision"))
        print("Reason:", result.get("reviewer_reason"))


def main():
    parser = argparse.ArgumentParser(
        description="Invoice Agent Workflow Runner"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("invoice_path")

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("thread_id")
    resume_parser.add_argument(
        "action",
        choices=["approve", "reject"],
    )
    resume_parser.add_argument(
        "--reason",
        default="",
    )

    args = parser.parse_args()

    if args.command == "start":
        start_invoice(args.invoice_path)

    elif args.command == "resume":
        resume_invoice(
            args.thread_id,
            args.action,
            args.reason,
        )


if __name__ == "__main__":
    main()
