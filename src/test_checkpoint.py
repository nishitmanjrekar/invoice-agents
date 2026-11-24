
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph


class TestState(TypedDict):
    message: str
    processed: bool


def process_node(state: TestState):
    return {
        "message": state["message"] + " | Checkpoint saved",
        "processed": True,
    }


def build_test_graph(checkpointer):
    graph = StateGraph(TestState)

    graph.add_node("process", process_node)

    graph.add_edge(START, "process")
    graph.add_edge("process", END)

    return graph.compile(checkpointer=checkpointer)


def main():
    print("LANGGRAPH SQLITE CHECKPOINT TEST")
    print("=" * 45)

    database_path = "checkpoints_test.sqlite"

    try:
        with SqliteSaver.from_conn_string(database_path) as checkpointer:
            checkpointer.setup()

            graph = build_test_graph(checkpointer)

            config = {
                "configurable": {
                    "thread_id": "checkpoint-smoke-test"
                }
            }

            initial_state = {
                "message": "Invoice workflow started",
                "processed": False,
            }

            result = graph.invoke(initial_state, config)

            print("\nWorkflow result:")
            print(result)

            saved_state = graph.get_state(config)

            print("\nRetrieved checkpoint:")
            print(saved_state.values)

            assert saved_state.values["processed"] is True
            assert "Checkpoint saved" in saved_state.values["message"]

            print("\n" + "=" * 45)
            print("SUCCESS: SQLite checkpoint is working!")
            print("Workflow state was saved and retrieved.")

    except Exception as error:
        print("\nCHECKPOINT TEST FAILED")
        print("Error type:", type(error).__name__)
        print("Details:", error)


if __name__ == "__main__":
    main()
