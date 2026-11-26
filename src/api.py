import json
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.encoders import jsonable_encoder
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy.engine import make_url

from src import auth, object_storage, submission_runner, submissions
from src.agents.invoice_graph import build_invoice_graph
from src.auth_routes import router as auth_router
from src.db import db_connection, get_database_summary
from src.db_core import get_database_backend
from src.review import make_decision, reject_review_case

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_DB = PROJECT_ROOT / "checkpoints.sqlite"
UPLOAD_DIR = PROJECT_ROOT / "data" / "api_uploads"

MAX_UPLOAD_SIZE = 2 * 1024 * 1024
STAFF_ROLES = ("admin", "finance_manager", "reviewer")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    if os.getenv("INVOICE_RECOVERY_ON_START", "1") == "1":
        threading.Thread(
            target=_recover_on_startup,
            name="submission-recovery",
            daemon=True,
        ).start()
    yield


app = FastAPI(
    title="AI Invoice Processing Agent",
    description=(
        "Invoice extraction, validation, MCP verification "
        "and human review API."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(auth_router)


class DecisionRequest(BaseModel):
    action: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=1000)


def get_graph(saver):
    saver.serde = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("src.schemas", "Invoice"),
        ]
    )

    return build_invoice_graph(
        checkpointer=saver,
        human_in_the_loop=True,
        use_mcp=True,
    )


@contextmanager
def open_checkpointer():
    """Open the configured LangGraph checkpoint backend."""

    if get_database_backend() == "postgresql":
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL is required for PostgreSQL checkpoints."
            )

        connection_url = make_url(database_url).set(
            drivername="postgresql"
        ).render_as_string(hide_password=False)

        with PostgresSaver.from_conn_string(connection_url) as saver:
            yield saver
    else:
        with SqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
            saver.setup()
            yield saver


@contextmanager
def open_graph():
    """Open the configured checkpointer and build the invoice graph."""

    with open_checkpointer() as saver:
        yield get_graph(saver)


def _list_checkpoint_threads(saver, limit: int) -> list[str]:
    """Return unique workflow IDs, ordered by their newest checkpoint."""

    thread_ids = []
    seen = set()
    before = None

    while len(thread_ids) < limit:
        batch = list(
            saver.list(
                None,
                before=before,
                limit=500,
            )
        )
        if not batch:
            break

        for checkpoint in batch:
            configurable = checkpoint.config.get("configurable", {})
            thread_id = configurable.get("thread_id")

            if thread_id and thread_id not in seen:
                seen.add(thread_id)
                thread_ids.append(thread_id)

                if len(thread_ids) >= limit:
                    break

        oldest_configurable = batch[-1].config.get("configurable", {})
        oldest_checkpoint_id = oldest_configurable.get("checkpoint_id")

        if not oldest_checkpoint_id:
            break

        before = {
            "configurable": {
                "checkpoint_id": oldest_checkpoint_id,
            }
        }

    return thread_ids


def _postgres_api_queries():
    """Import PostgreSQL-only API query helpers lazily."""

    from src import api_queries_postgres

    return api_queries_postgres


# ---------------------------------------------------------------- ledger helpers


def _ledger_ready() -> bool:
    """False while the submissions ledger migration is unavailable."""

    try:
        return submissions.ledger_available()
    except Exception:
        logger.exception("Could not check for the submissions table")
        return False


def _submission_for(thread_id):
    if not _ledger_ready():
        return None

    try:
        return submissions.get_by_thread(thread_id)
    except Exception:
        logger.exception("Could not read submission for %s", thread_id)
        return None


def _authenticated_user(user):
    """Return a resolved user dict, or None for direct legacy calls."""

    return user if isinstance(user, dict) else None


def _is_enforced_uploader(user):
    """Apply ownership restrictions only when authentication is enforced."""

    user = _authenticated_user(user)

    return (
        auth.auth_enforced()
        and user is not None
        and user["role"] == "uploader"
    )


def _mark_decided(thread_id):
    """After a human decision the workflow is finished: close the ledger row."""

    try:
        row = _submission_for(thread_id)

        if row and row["status"] == "awaiting_approval":
            submissions.set_status(thread_id, "completed")

    except Exception:
        logger.exception("Could not update submission ledger for %s", thread_id)


def _recover_on_startup():
    if not _ledger_ready():
        logger.info(
            "Submissions ledger not available (migration 002 not applied); "
            "startup recovery skipped."
        )
        return None

    if get_database_backend() == "postgresql":
        try:
            object_storage.check_storage_ready()
        except Exception:
            logger.exception(
                "Object storage is unavailable; startup recovery skipped."
            )
            return None

    try:
        grace = float(os.getenv("SUBMISSION_RECOVERY_GRACE_SECONDS", "0"))

        return submission_runner.recover_submissions(
            open_graph,
            grace_seconds=grace,
        )

    except Exception:
        logger.exception("Startup recovery failed")
        return None


def _run_legacy_workflow(invoice_path: str, thread_id: str):
    """Original behaviour, used only while the ledger table does not exist."""

    config = {"configurable": {"thread_id": thread_id}}

    try:
        with open_graph() as graph:
            result = graph.invoke({"invoice_path": invoice_path}, config)

            logger.info(
                "Workflow %s completed with status %s",
                thread_id,
                result.get("status"),
            )

    except Exception:
        logger.exception("Invoice workflow failed: %s", thread_id)


def run_invoice_workflow(invoice_path: str, thread_id: str):
    """Run an invoice workflow in a background task (ledger-aware)."""

    if _ledger_ready():
        submission_runner.run_submission(thread_id, invoice_path, open_graph)
    else:
        _run_legacy_workflow(invoice_path, thread_id)


# ---------------------------------------------------------------- public routes


@app.get("/")
def root():
    return {
        "message": "AI Invoice Processing Agent API",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health(user=Depends(auth.require_access())):
    try:
        summary = get_database_summary()

        if _is_enforced_uploader(user):
            return {
                "status": "healthy",
                "database": "connected",
            }

        return {
            "status": "healthy",
            "database": "connected",
            "records": summary,
        }

    except Exception:
        logger.exception("Health check failed")

        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )


# ---------------------------------------------------------------- invoice upload


@app.post("/invoices/process", status_code=202)
async def process_invoice(
    background_tasks: BackgroundTasks,
    response: Response,
    file: UploadFile = File(...),
    resubmit_of: int | None = Query(default=None, ge=1),
    user=Depends(auth.require_access()),
):
    """Upload a text invoice and start its workflow."""

    if (
        auth.auth_enforced()
        and resubmit_of is not None
        and user["role"] not in STAFF_ROLES
    ):
        raise HTTPException(
            status_code=403,
            detail="Only staff can resubmit invoices.",
        )

    if Path(file.filename or "").suffix.lower() != ".txt":
        raise HTTPException(
            status_code=400,
            detail="Only .txt invoice files are supported.",
        )

    content = await file.read(MAX_UPLOAD_SIZE + 1)
    await file.close()

    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Invoice file exceeds the 2 MB limit.",
        )

    if not content.strip():
        raise HTTPException(
            status_code=400,
            detail="Invoice file is empty.",
        )

    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invoice must be a valid UTF-8 text file.",
        )

    is_postgres = get_database_backend() == "postgresql"
    ledger_ready = _ledger_ready()

    if (is_postgres or auth.auth_enforced()) and not ledger_ready:
        raise HTTPException(
            status_code=503,
            detail="Submission service is not ready.",
        )

    thread_id = str(uuid.uuid4())

    if is_postgres:
        stored_path = f"invoices/{thread_id}.txt"

        try:
            object_storage.upload_invoice(stored_path, content)

        except Exception:
            logger.exception("Could not upload invoice to Supabase Storage")

            raise HTTPException(
                status_code=503,
                detail="Invoice storage is temporarily unavailable.",
            )

        def discard_file():
            try:
                object_storage.delete_invoice(stored_path)
            except Exception:
                logger.exception(
                    "Could not clean up Supabase Storage object %s",
                    stored_path,
                )

    else:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

        invoice_path = UPLOAD_DIR / f"{thread_id}.txt"
        invoice_path.write_bytes(content)
        stored_path = str(invoice_path)

        def discard_file():
            invoice_path.unlink(missing_ok=True)

    if not ledger_ready:
        background_tasks.add_task(
            run_invoice_workflow,
            stored_path,
            thread_id,
        )

        return {
            "message": "Invoice accepted for processing.",
            "thread_id": thread_id,
            "status": "queued",
        }

    sha256 = submissions.hash_bytes(content)

    authenticated_user = _authenticated_user(user)
    submitted_by = (
        str(authenticated_user["id"])
        if auth.auth_enforced() and authenticated_user
        else None
    )

    try:
        row, created = submissions.register_submission(
            thread_id,
            sha256,
            file.filename or "invoice.txt",
            mime="text/plain",
            stored_path=stored_path,
            resubmit_of=resubmit_of,
            submitted_by=submitted_by,
        )

    except submissions.ResubmissionRequired as exc:
        discard_file()

        if _is_enforced_uploader(user):
            raise HTTPException(
                status_code=409,
                detail="This file was previously submitted. Contact staff if it needs to be processed again.",
            )

        failed = exc.existing

        raise HTTPException(
            status_code=409,
            detail=(
                f"A previous submission of this file (submission {failed['id']}) "
                f"failed: {failed.get('error') or 'no error recorded'}. "
                f"To process it again, resubmit explicitly with "
                f"resubmit_of={failed['id']}."
            ),
        )

    except submissions.SubmissionNotFound as exc:
        discard_file()
        raise HTTPException(status_code=404, detail=str(exc))

    except submissions.HashMismatch as exc:
        discard_file()
        raise HTTPException(status_code=400, detail=str(exc))

    except submissions.SubmissionError as exc:
        discard_file()

        if _is_enforced_uploader(user):
            raise HTTPException(
                status_code=409,
                detail="Could not register this submission.",
            )

        raise HTTPException(status_code=409, detail=str(exc))

    except Exception:
        discard_file()
        logger.exception("Could not register submission")

        raise HTTPException(
            status_code=500,
            detail="Could not record the submission.",
        )

    if not created:
        discard_file()

        if (
            _is_enforced_uploader(user)
            and row.get("submitted_by") != str(user["id"])
        ):
            raise HTTPException(
                status_code=409,
                detail="This file was already submitted.",
            )

        response.status_code = 200

        return {
            "message": (
                f"This file was already submitted (submission {row['id']}, "
                f"status {row['status']}). No new workflow was started."
            ),
            "thread_id": row["thread_id"],
            "status": row["status"],
            "submission_id": row["id"],
            "already_submitted": True,
        }

    background_tasks.add_task(
        run_invoice_workflow,
        stored_path,
        thread_id,
    )

    return {
        "message": "Invoice accepted for processing.",
        "thread_id": thread_id,
        "status": "queued",
        "submission_id": row["id"],
        "already_submitted": False,
        "resubmission_of": row["resubmission_of"],
    }


# ---------------------------------------------------------------- workflow detail


@app.get("/workflows/{thread_id}")
def get_workflow_status(
    thread_id: str,
    user=Depends(auth.require_access()),
):
    """Retrieve the saved LangGraph workflow state."""

    submission = _submission_for(thread_id)

    if (
        _is_enforced_uploader(user)
        and (
            submission is None
            or submission.get("submitted_by") != str(user["id"])
        )
    ):
        raise HTTPException(
            status_code=404,
            detail="Workflow not found.",
        )

    config = {"configurable": {"thread_id": thread_id}}

    try:
        with open_graph() as graph:
            state = graph.get_state(config)

            failed_submission = (
                submission
                if submission and submission["status"] == "failed"
                else None
            )

            if not state.values:
                if failed_submission:
                    return {
                        "thread_id": thread_id,
                        "status": "processing_failed",
                        "invoice_id": None,
                        "error_stage": "submission",
                        "error_message": (
                            failed_submission["error"] or "Processing failed."
                        ),
                        "next_nodes": [],
                        "approval_required": False,
                        "completed": True,
                        "route_reason": None,
                        "vendor_approved": None,
                        "is_duplicate": None,
                        "mcp_verified": None,
                        "review_case_id": None,
                        "reviewer_decision": None,
                        "reviewer_reason": None,
                        "validation_report": None,
                        "submission_id": failed_submission["id"],
                        "submission_status": "failed",
                    }

                raise HTTPException(
                    status_code=404,
                    detail="Workflow not found.",
                )

            values = state.values

            return {
                "thread_id": thread_id,
                "status": values.get("status"),
                "invoice_id": values.get("invoice_id"),
                "error_stage": values.get("error_stage")
                or ("submission" if failed_submission else None),
                "error_message": values.get("error_message")
                or (failed_submission["error"] if failed_submission else None),
                "next_nodes": list(state.next),
                "approval_required": "human_approval" in state.next,
                "completed": not bool(state.next),
                "route_reason": values.get("route_reason"),
                "vendor_approved": values.get("vendor_approved"),
                "is_duplicate": values.get("is_duplicate"),
                "mcp_verified": values.get("mcp_verified"),
                "review_case_id": values.get("review_case_id"),
                "reviewer_decision": values.get("reviewer_decision"),
                "reviewer_reason": values.get("reviewer_reason"),
                "validation_report": jsonable_encoder(
                    values.get("validation_report")
                ),
                "submission_id": submission["id"] if submission else None,
                "submission_status": submission["status"] if submission else None,
            }

    except HTTPException:
        raise

    except Exception:
        logger.exception("Could not retrieve workflow")

        raise HTTPException(
            status_code=500,
            detail="Could not retrieve workflow state.",
        )


# ---------------------------------------------------------------- workflow list


def _workflow_summary(thread_id: str, state, ledger_row=None) -> dict:
    values = state.values

    return {
        "thread_id": thread_id,
        "invoice_id": values.get("invoice_id"),
        "status": values.get("status"),
        "approval_required": "human_approval" in state.next,
        "completed": not bool(state.next),
        "review_case_id": values.get("review_case_id"),
        "submission_id": ledger_row["id"] if ledger_row else None,
        "submission_status": ledger_row["status"] if ledger_row else None,
    }


@app.get("/workflows")
def list_workflows(
    limit: int = Query(default=200, ge=1, le=500),
    user=Depends(auth.require_access()),
):
    """List saved workflows, newest first, with their submission ledger data."""

    try:
        ledger = {}

        if _ledger_ready():
            if get_database_backend() == "postgresql":
                rows = _postgres_api_queries().list_submissions()
            else:
                with db_connection() as conn:
                    rows = [
                        dict(row)
                        for row in conn.execute(
                            """
                            SELECT id, thread_id, status, submitted_by
                            FROM submissions
                            WHERE thread_id IS NOT NULL
                            """
                        )
                    ]

            if _is_enforced_uploader(user):
                rows = [
                    row
                    for row in rows
                    if row.get("submitted_by") == str(user["id"])
                ]

            ledger = {
                row["thread_id"]: {
                    "id": row["id"],
                    "status": row["status"],
                }
                for row in rows
            }

        with open_checkpointer() as saver:
            graph = get_graph(saver)
            thread_ids = _list_checkpoint_threads(saver, limit)

            workflows = []
            skipped = 0

            for thread_id in thread_ids:
                if (
                    _is_enforced_uploader(user)
                    and thread_id not in ledger
                ):
                    continue

                config = {"configurable": {"thread_id": thread_id}}

                try:
                    state = graph.get_state(config)

                except Exception:
                    logger.exception(
                        "Could not read workflow %s",
                        thread_id,
                    )
                    skipped += 1
                    continue

                if not state.values:
                    skipped += 1
                    continue

                workflows.append(
                    _workflow_summary(
                        thread_id,
                        state,
                        ledger.get(thread_id),
                    )
                )

    except Exception:
        logger.exception("Could not list workflows")

        raise HTTPException(
            status_code=500,
            detail="Could not list workflows.",
        )

    return {
        "count": len(workflows),
        "skipped": skipped,
        "workflows": workflows,
    }


# ---------------------------------------------------------------- workflow decisions


@app.post("/workflows/{thread_id}/decision")
def decide_workflow(
    thread_id: str,
    request: DecisionRequest,
    user=Depends(auth.require_access(*STAFF_ROLES)),
):
    """Resume a paused workflow with a human decision."""

    if request.action == "reject" and not request.reason.strip():
        raise HTTPException(
            status_code=400,
            detail="A rejection reason is required.",
        )

    submission = _submission_for(thread_id)

    if (
        auth.auth_enforced()
        and request.action == "approve"
        and submission is not None
        and submission.get("submitted_by") == str(user["id"])
    ):
        raise HTTPException(
            status_code=403,
            detail="You cannot approve your own invoice submission.",
        )

    config = {"configurable": {"thread_id": thread_id}}

    try:
        with open_graph() as graph:
            saved_state = graph.get_state(config)

            if not saved_state.values:
                raise HTTPException(
                    status_code=404,
                    detail="Workflow not found.",
                )

            if not saved_state.next:
                raise HTTPException(
                    status_code=409,
                    detail="Workflow is already completed.",
                )

            if "human_approval" not in saved_state.next:
                raise HTTPException(
                    status_code=409,
                    detail="Workflow is not waiting for human approval.",
                )

            result = graph.invoke(
                Command(
                    update={"review_actor": (user or {}).get("username") or "human_reviewer"},
                    resume={
                        "action": request.action,
                        "reason": request.reason.strip(),
                    }
                ),
                config,
            )

        _mark_decided(thread_id)

        return {
            "message": "Human decision recorded.",
            "thread_id": thread_id,
            "decision": request.action,
            "status": result.get("status"),
            "invoice_id": result.get("invoice_id"),
        }

    except HTTPException:
        raise

    except Exception:
        logger.exception("Workflow decision failed")

        raise HTTPException(
            status_code=500,
            detail="Could not record workflow decision.",
        )


# ---------------------------------------------------------------- invoice list


@app.get("/invoices")
def list_invoices(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user=Depends(auth.require_access()),
):
    user_id = str(user["id"]) if _is_enforced_uploader(user) else None

    if get_database_backend() == "postgresql":
        rows, total = _postgres_api_queries().list_invoices(
            limit,
            offset,
            user_id=user_id,
        )

    else:
        with db_connection() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        i.id,
                        i.invoice_number,
                        v.vendor_name,
                        i.invoice_date,
                        i.currency,
                        i.total,
                        i.po_number,
                        i.status,
                        i.created_at
                    FROM invoices AS i
                    LEFT JOIN vendors AS v ON i.vendor_id = v.id
                    WHERE (
                        ? IS NULL
                        OR EXISTS (
                            SELECT 1
                            FROM submissions AS s
                            WHERE s.thread_id = i.thread_id
                              AND s.submitted_by = ?
                        )
                    )
                    ORDER BY i.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (user_id, user_id, limit, offset),
                )
            ]

            total = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM invoices AS i
                WHERE (
                    ? IS NULL
                    OR EXISTS (
                        SELECT 1
                        FROM submissions AS s
                        WHERE s.thread_id = i.thread_id
                          AND s.submitted_by = ?
                    )
                )
                """,
                (user_id, user_id),
            ).fetchone()["total"]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "invoices": rows,
    }


# ---------------------------------------------------------------- invoice detail


@app.get("/invoices/{invoice_id}")
def get_invoice(
    invoice_id: int,
    user=Depends(auth.require_access()),
):
    user_id = str(user["id"]) if _is_enforced_uploader(user) else None

    if get_database_backend() == "postgresql":
        row, logs = _postgres_api_queries().get_invoice(
            invoice_id,
            user_id=user_id,
        )

    else:
        with db_connection() as conn:
            row = conn.execute(
                """
                SELECT i.*, v.vendor_name
                FROM invoices AS i
                LEFT JOIN vendors AS v ON i.vendor_id = v.id
                WHERE i.id = ?
                  AND (
                      ? IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM submissions AS s
                          WHERE s.thread_id = i.thread_id
                            AND s.submitted_by = ?
                      )
                  )
                """,
                (invoice_id, user_id, user_id),
            ).fetchone()

            if row is not None:
                row = dict(row)

            logs = (
                [
                    dict(log)
                    for log in conn.execute(
                        """
                        SELECT action, actor, details, created_at
                        FROM audit_logs
                        WHERE invoice_id = ?
                        ORDER BY id
                        """,
                        (invoice_id,),
                    )
                ]
                if row is not None and user_id is None
                else []
            )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Invoice not found.",
        )

    invoice = dict(row)

    try:
        invoice["extracted_data"] = json.loads(invoice["extracted_data"])
    except (TypeError, json.JSONDecodeError, KeyError):
        pass

    return {
        "invoice": invoice,
        "audit_history": logs,
    }


# ---------------------------------------------------------------- invoice rejection


@app.post("/invoices/{invoice_id}/reject")
def reject_human_review_invoice(
    invoice_id: int,
    request: DecisionRequest,
    user=Depends(auth.require_access(*STAFF_ROLES)),
):
    """
    Reject an invoice whose status is human_review.

    Approval is deliberately not offered: these invoices have unresolved
    validation or security issues. Their workflow has already finished, so
    there is nothing to resume. Pending-approval invoices must still be
    decided through POST /workflows/{thread_id}/decision.
    """

    if request.action != "reject":
        raise HTTPException(
            status_code=400,
            detail=(
                "Only rejection is supported for invoices in human review. "
                "They have unresolved validation or security issues."
            ),
        )

    reason = request.reason.strip()

    if not reason:
        raise HTTPException(
            status_code=400,
            detail="A rejection reason is required.",
        )

    if get_database_backend() == "postgresql":
        row = _postgres_api_queries().get_invoice_status(invoice_id)

    else:
        with db_connection() as conn:
            result = conn.execute(
                "SELECT id, status FROM invoices WHERE id = ?",
                (invoice_id,),
            ).fetchone()

            row = dict(result) if result is not None else None

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Invoice not found.",
        )

    if row["status"] != "human_review":
        raise HTTPException(
            status_code=409,
            detail=(
                "Only invoices in human review can be rejected here. "
                "Pending-approval invoices are decided through their workflow."
            ),
        )

    if not make_decision(invoice_id, "reject", reason, actor=(user or {}).get("username") or "human_reviewer",):
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not record the rejection. "
                "The invoice status may have changed."
            ),
        )

    return {
        "message": "Invoice rejected.",
        "invoice_id": invoice_id,
        "status": "rejected",
    }


# ---------------------------------------------------------------- review cases


@app.get("/review-cases")
def list_review_cases(
    status: Literal["open", "rejected", "all"] = "open",
    user=Depends(auth.require_access(*STAFF_ROLES)),
):
    if get_database_backend() == "postgresql":
        rows = _postgres_api_queries().list_review_cases(status)

    else:
        with db_connection() as conn:
            if status == "all":
                rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT *
                        FROM invoice_review_cases
                        ORDER BY id DESC
                        """
                    )
                ]

            else:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT *
                        FROM invoice_review_cases
                        WHERE status = ?
                        ORDER BY id DESC
                        """,
                        (status,),
                    )
                ]

    return {
        "count": len(rows),
        "review_cases": rows,
    }


@app.post("/review-cases/{case_id}/reject")
def reject_case(
    case_id: int,
    request: DecisionRequest,
    user=Depends(auth.require_access(*STAFF_ROLES)),
):
    """Reject an open review case. Review cases cannot be approved."""

    if request.action != "reject":
        raise HTTPException(
            status_code=400,
            detail=(
                "Only rejection is supported for review cases. "
                "Unknown vendors and duplicates require investigation."
            ),
        )

    reason = request.reason.strip()

    if not reason:
        raise HTTPException(
            status_code=400,
            detail="A rejection reason is required.",
        )

    if get_database_backend() == "postgresql":
        case = _postgres_api_queries().get_review_case(case_id)

    else:
        with db_connection() as conn:
            result = conn.execute(
                """
                SELECT id, status
                FROM invoice_review_cases
                WHERE id = ?
                """,
                (case_id,),
            ).fetchone()

            case = dict(result) if result is not None else None

    if case is None:
        raise HTTPException(
            status_code=404,
            detail="Review case not found.",
        )

    if case["status"] != "open":
        raise HTTPException(
            status_code=409,
            detail="Review case is no longer open.",
        )

    success = reject_review_case(case_id, reason, actor=(user or {}).get("username") or "human_reviewer",)

    if not success:
        raise HTTPException(
            status_code=409,
            detail="Could not reject review case.",
        )

    return {
        "message": "Review case rejected.",
        "case_id": case_id,
        "status": "rejected",
    }
