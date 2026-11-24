
import asyncio
import json
from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, model_validator


class ApprovalDecision(BaseModel):
    action: Literal["approve", "reject"]
    reason: str = ""

    @model_validator(mode="after")
    def require_rejection_reason(self):
        if self.action == "reject" and not self.reason.strip():
            raise ValueError("A rejection reason is required")
        return self


class InvoiceState(TypedDict, total=False):
    invoice_path: str
    invoice_text: str
    invoice: Any
    validation_report: dict
    status: str
    invoice_id: int | None
    review_case_id: int | None
    vendor_approved: bool
    is_duplicate: bool
    mcp_verified: bool
    route_reason: str

    error_stage: str
    error_type: str
    error_message: str
    audit_logged: bool
    audit_log_error: str

    reviewer_decision: str
    reviewer_reason: str


def extract_invoice_node(
    state: InvoiceState,
    extractor=None,
    config: RunnableConfig | None = None,
) -> dict:
    try:
        if extractor is None:
            from src.extract import extract_invoice as extractor

        invoice_path = Path(state["invoice_path"])

        if not invoice_path.is_absolute():
            invoice_path = (
                Path(__file__).resolve().parent.parent.parent
                / invoice_path
            )

        invoice_text = invoice_path.read_text(encoding="utf-8")
        from src import telemetry

        thread_id = ((config or {}).get("configurable") or {}).get("thread_id")

        with telemetry.bind_thread(thread_id):
            invoice = extractor(invoice_text)

        return {
            "invoice_text": invoice_text,
            "invoice": invoice,
        }

    except Exception as error:
        return {
            "error_stage": "extraction",
            "error_type": type(error).__name__,
            "error_message": str(error),
        }


def validate_invoice_node(state: InvoiceState) -> dict:
    try:
        from src.rules import validate_invoice

        report = validate_invoice(
            state["invoice"],
            state["invoice_text"],
        )

        return {"validation_report": report}

    except Exception as error:
        return {
            "error_stage": "validation",
            "error_type": type(error).__name__,
            "error_message": str(error),
        }


def check_database_node(
    state: InvoiceState,
    use_mcp: bool = False,
) -> dict:
    try:
        invoice = state["invoice"]

        if use_mcp:
            from src.mcp_servers.client import verify_invoice_with_mcp

            result = asyncio.run(
                verify_invoice_with_mcp(
                    invoice.vendor,
                    invoice.invoice_number,
                )
            )

            return {
                "vendor_approved": result["vendor_approved"],
                "is_duplicate": result["is_duplicate"],
                "mcp_verified": True,
            }

        from src.db import find_duplicate, get_vendor
        vendor = get_vendor(invoice.vendor)

        if not vendor:
            return {
                "vendor_approved": False,
                "is_duplicate": False,
                "mcp_verified": False,
            }

        duplicate = find_duplicate(
            invoice.vendor,
            invoice.invoice_number,
        )

        return {
            "vendor_approved": bool(vendor["is_approved"]),
            "is_duplicate": duplicate is not None,
            "mcp_verified": False,
        }

    except Exception as error:
        return {
            "error_stage": "database_check",
            "error_type": type(error).__name__,
            "error_message": str(error),
        }


def route_invoice_node(state: InvoiceState) -> dict:
    if not state["vendor_approved"]:
        return {
            "status": "human_review",
            "route_reason": "Unknown or unapproved vendor",
        }

    if state["is_duplicate"]:
        return {
            "status": "human_review",
            "route_reason": "Duplicate invoice detected",
        }

    report = state["validation_report"]

    if report["requires_human_review"]:
        return {
            "status": "human_review",
            "route_reason": "Business or security validation issues",
        }

    return {
        "status": "pending_approval",
        "route_reason": "All automated validation checks passed",
    }


def continue_or_fail(state: InvoiceState) -> str:
    if state.get("error_stage"):
        return "handle_failure"

    return "continue"


def choose_storage_path(state: InvoiceState) -> str:
    if not state["vendor_approved"] or state["is_duplicate"]:
        return "log_review"

    return "save_invoice"


def handle_failure_node(state: InvoiceState) -> dict:
    from src.db import log_audit_event

    stage = state.get("error_stage", "unknown")
    error_type = state.get("error_type", "UnknownError")
    error_message = state.get(
        "error_message",
        "Unknown processing error",
    )

    failure_details = {
        "stage": stage,
        "error_type": error_type,
        "error_message": error_message,
        "invoice_path": state.get("invoice_path"),
    }

    result = {
        "status": "processing_failed",
        "invoice_id": None,
        "route_reason": f"{stage} failed: {error_message}",
        "audit_logged": False,
    }

    try:
        log_audit_event(
            None,
            "PROCESSING_FAILED",
            "SYSTEM",
            json.dumps(failure_details),
        )
        result["audit_logged"] = True

    except Exception as audit_error:
        result["audit_log_error"] = str(audit_error)

    return result


def save_invoice_node(
    state: InvoiceState,
    config: RunnableConfig | None = None,
) -> dict:
    """
    Save the validated invoice.

    Replay-safe: with a workflow thread_id and migration 003 applied, saving is
    idempotent per thread, so a replayed node returns the existing invoice
    instead of tripping the duplicate rule. Otherwise the original behaviour
    is used unchanged.
    """

    from src import invoice_store
    from src.db import log_audit_event, save_invoice

    invoice = state["invoice"]
    thread_id = ((config or {}).get("configurable") or {}).get("thread_id")

    try:
        if thread_id and invoice_store.has_thread_column():
            invoice_id, _created = invoice_store.save_invoice_once(
                invoice,
                state["status"],
                str(thread_id),
            )
        else:
            invoice_id = save_invoice(
                invoice,
                status=state["status"],
            )

        return {"invoice_id": invoice_id}

    except ValueError as error:
        result = {
            "invoice_id": None,
            "status": "human_review",
            "route_reason": str(error),
            "audit_logged": False,
        }

        try:
            log_audit_event(
                None,
                "INVOICE_SAVE_REQUIRES_REVIEW",
                "SYSTEM",
                json.dumps({
                    "invoice_number": invoice.invoice_number,
                    "vendor": invoice.vendor,
                    "error": str(error),
                }),
            )
            result["audit_logged"] = True

        except Exception as audit_error:
            result["audit_log_error"] = str(audit_error)

        return result

    except Exception as error:
        result = {
            "invoice_id": None,
            "status": "processing_failed",
            "error_stage": "database_save",
            "error_type": type(error).__name__,
            "error_message": str(error),
            "route_reason": f"Database save failed: {error}",
            "audit_logged": False,
        }

        try:
            log_audit_event(
                None,
                "INVOICE_SAVE_FAILED",
                "SYSTEM",
                json.dumps({
                    "invoice_number": invoice.invoice_number,
                    "vendor": invoice.vendor,
                    "error": str(error),
                }),
            )
            result["audit_logged"] = True

        except Exception as audit_error:
            result["audit_log_error"] = str(audit_error)

        return result


def log_review_node(state: InvoiceState) -> dict:
    from src.db import create_review_case

    invoice = state["invoice"]

    if not state.get("vendor_approved", False):
        case_type = "unknown_vendor"
    elif state.get("is_duplicate", False):
        case_type = "duplicate"
    else:
        return {
            "invoice_id": None,
            "status": "processing_failed",
            "error_stage": "review_case_routing",
            "error_type": "InvalidReviewRoute",
            "error_message": (
                "Invoice was routed to review-case storage "
                "without an unknown vendor or duplicate condition."
            ),
            "route_reason": "Invalid review-case routing condition",
            "audit_logged": False,
        }

    try:
        case_id = create_review_case(
            invoice=invoice,
            validation_report=state["validation_report"],
            case_type=case_type,
        )

        return {
            "invoice_id": None,
            "review_case_id": case_id,
            "audit_logged": True,
        }

    except Exception as error:
        return {
            "invoice_id": None,
            "review_case_id": None,
            "status": "processing_failed",
            "error_stage": "review_case_save",
            "error_type": type(error).__name__,
            "error_message": str(error),
            "route_reason": f"Review case could not be saved: {error}",
            "audit_logged": False,
        }


def after_review(state: InvoiceState) -> str:
    if state.get("error_stage"):
        return "handle_failure"

    return "finish"


def human_approval_node(state: InvoiceState) -> dict:
    """
    Pause the graph until a human approves or rejects
    an eligible invoice.
    """

    decision = interrupt(
        {
            "type": "invoice_approval",
            "invoice_id": state["invoice_id"],
            "invoice_number": state["invoice"].invoice_number,
            "vendor": state["invoice"].vendor,
            "amount": float(state["invoice"].total),
            "status": state["status"],
            "available_actions": ["approve", "reject"],
        },
        response_schema=ApprovalDecision,
    )

    action = decision.action
    reason = decision.reason

    from src.review import make_decision

    success = make_decision(
        invoice_id=state["invoice_id"],
        decision=action,
        reason=reason,
    )

    if not success:
        raise RuntimeError(
            "Human decision could not be recorded in the database"
        )

    return {
        "status": "approved" if action == "approve" else "rejected",
        "reviewer_decision": action,
        "reviewer_reason": reason,
        "route_reason": f"Human reviewer selected: {action}",
    }


def after_save(state: InvoiceState, human_in_the_loop: bool) -> str:
    if (
        human_in_the_loop
        and state.get("status") == "pending_approval"
        and state.get("invoice_id") is not None
    ):
        return "human_approval"

    return "finish"


def build_invoice_graph(
    checkpointer=None,
    human_in_the_loop: bool = False,
    use_mcp: bool = False,
    *,
    extractor=None,
):
    graph = StateGraph(InvoiceState)

    graph.add_node(
        "extract_invoice",
        lambda state, config: extract_invoice_node(
            state,
            extractor=extractor,
            config=config,
        ),
    )
    graph.add_node("validate_invoice", validate_invoice_node)

    graph.add_node(
        "check_database",
        lambda state: check_database_node(state, use_mcp=use_mcp),
    )

    graph.add_node("route_invoice", route_invoice_node)
    graph.add_node("save_invoice", save_invoice_node)
    graph.add_node("log_review", log_review_node)
    graph.add_node("handle_failure", handle_failure_node)
    graph.add_node("human_approval", human_approval_node)

    graph.add_edge(START, "extract_invoice")

    graph.add_conditional_edges(
        "extract_invoice",
        continue_or_fail,
        {
            "continue": "validate_invoice",
            "handle_failure": "handle_failure",
        },
    )

    graph.add_conditional_edges(
        "validate_invoice",
        continue_or_fail,
        {
            "continue": "check_database",
            "handle_failure": "handle_failure",
        },
    )

    graph.add_conditional_edges(
        "check_database",
        continue_or_fail,
        {
            "continue": "route_invoice",
            "handle_failure": "handle_failure",
        },
    )

    graph.add_conditional_edges(
        "route_invoice",
        choose_storage_path,
        {
            "save_invoice": "save_invoice",
            "log_review": "log_review",
        },
    )

    graph.add_conditional_edges(
        "save_invoice",
        lambda state: after_save(state, human_in_the_loop),
        {
            "human_approval": "human_approval",
            "finish": END,
        },
    )

    graph.add_edge("human_approval", END)

    graph.add_conditional_edges(
        "log_review",
        after_review,
        {
            "finish": END,
            "handle_failure": "handle_failure",
        },
    )

    graph.add_edge("handle_failure", END)

    return graph.compile(checkpointer=checkpointer)


# Existing tests and offline usage continue to use this graph.
invoice_graph = build_invoice_graph()
