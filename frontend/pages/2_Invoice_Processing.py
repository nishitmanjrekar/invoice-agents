import hashlib
import re
import time

import api_client
import streamlit as st
from api_client import ApiError
from components.invoice_view import render_invoice_detail
from components.status import get_status_label

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
POLL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 120

STATE_KEYS = (
    "proc_thread_id", "proc_digest", "proc_filename", "proc_started",
    "proc_workflow", "proc_note", "proc_error", "proc_notice", "proc_resubmit",
)

for key in STATE_KEYS:
    st.session_state.setdefault(key, None)
st.session_state.setdefault("proc_finished", False)
st.session_state.setdefault("uploader_nonce", 0)

state = st.session_state

st.title("Invoice Processing")
st.caption("Upload a .txt invoice. It is submitted once and tracked by its workflow ID.")


def start_new():
    for key in STATE_KEYS:
        state[key] = None
    state["proc_finished"] = False
    state["uploader_nonce"] += 1  # clears the file uploader


def recheck():
    state["proc_finished"] = False
    state["proc_started"] = time.time()
    state["proc_note"] = None


def validate(content: bytes):
    if len(content) > MAX_UPLOAD_BYTES:
        return "The file exceeds the 2 MB limit."
    if not content.strip():
        return "The file is empty."
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "The file must be valid UTF-8 text."
    return None


def is_settled(workflow):
    submission_status = workflow.get("submission_status")

    if submission_status is not None:
        return submission_status in {
            "awaiting_approval",
            "completed",
            "failed",
            "review_case",
        }

    # Backward compatibility when the submission ledger is unavailable.
    return bool(
        workflow.get("completed")
        or workflow.get("approval_required")
        or workflow.get("error_message")
        or workflow.get("error_stage")
    )

def do_submit(resubmit_of=None):
    """
    Runs once per click, as a button callback. The digest is recorded as
    'submitted' BEFORE the request is sent, so a second click that arrives
    while this one is in flight finds the lock and does nothing.
    """
    uploaded = state.get(f"invoice_file_{state.uploader_nonce}")
    if uploaded is None:
        return

    content = uploaded.getvalue()
    digest = hashlib.sha256(content).hexdigest()

    if state.proc_digest == digest:  # already sent (or sending)
        return

    state.proc_error = None
    state.proc_notice = None
    state.proc_resubmit = None

    problem = validate(content)
    if problem:
        state.proc_error = ("error", problem)
        return

    state.proc_digest = digest  # lock first, then send

    try:
        result = api_client.upload_invoice(uploaded.name, content, resubmit_of=resubmit_of)
    except ApiError as exc:
        match = re.search(r"resubmit_of=(\d+)", exc.message)

        if exc.status_code == 409 and match:
            # An earlier submission of this file failed: offer an explicit resubmission.
            state.proc_digest = None
            state.proc_resubmit = int(match.group(1))
            state.proc_error = ("warning", exc.message)
        elif exc.status_code in (400, 404, 409, 413):
            state.proc_digest = None  # rejected before anything was created: unlock
            state.proc_error = ("error", exc.message)
        elif exc.status_code is None:
            state.proc_error = (
                "warning",
                f"Could not confirm whether the upload was received "
                f"({exc.message}). It stays locked to avoid a duplicate. "
                "Check Invoice Management or the uvicorn log, then click "
                "Start new submission if it was not received.",
            )
        else:
            state.proc_error = ("error", exc.message)
        return

    state.proc_thread_id = result["thread_id"]
    state.proc_filename = uploaded.name
    state.proc_started = time.time()
    state.proc_workflow = None
    state.proc_note = None
    state.proc_finished = False

    if result.get("already_submitted"):
        state.proc_notice = (
            "info",
            result.get("message")
            or f"This file was already submitted (submission {result.get('submission_id')}). "
            "No new workflow was started. Showing the existing one.",
        )
    elif result.get("resubmission_of"):
        state.proc_notice = (
            "success",
            f"Resubmitted as submission {result.get('submission_id')} "
            f"(linked to submission {result['resubmission_of']}). "
            "The earlier record is unchanged.",
        )


# ---------------------------------------------------------------- submit
tracking_active = state.proc_thread_id is not None and not state.proc_finished

uploaded = st.file_uploader(
    "Invoice file (.txt, max 2 MB)",
    type=["txt"],
    disabled=tracking_active,
    key=f"invoice_file_{state.uploader_nonce}",
)

content = uploaded.getvalue() if uploaded else None
digest = hashlib.sha256(content).hexdigest() if content is not None else None
already_submitted = digest is not None and digest == state.proc_digest

if already_submitted and not tracking_active:
    st.info(
        "This file was already submitted in this session. "
        "Choose another file, or click Start new submission to submit it again."
    )

left, right = st.columns([1, 1])

left.button(
    "Upload and process",
    type="primary",
    icon=":material/upload:",
    on_click=do_submit,
    disabled=(uploaded is None or tracking_active or already_submitted),
)
right.button(
    "Start new submission",
    on_click=start_new,
    disabled=tracking_active,
)

if state.proc_error:
    kind, text = state.proc_error
    getattr(st, kind)(text)

if state.proc_resubmit and uploaded is not None and not tracking_active:
    st.button(
        f"Resubmit explicitly (earlier submission {state.proc_resubmit})",
        on_click=do_submit,
        args=(state.proc_resubmit,),
        icon=":material/replay:",
    )
    st.caption(
        "A resubmission creates a new linked record. The failed one stays as it is "
        "in the audit history."
    )


# --------------------------------------------------------------- tracking
def tracker():
    """Polls ONLY the workflow status endpoint. Never submits anything."""
    elapsed = time.time() - state.proc_started
    workflow = None

    try:
        workflow = api_client.get_workflow(state.proc_thread_id)
    except ApiError as exc:
        if exc.status_code != 404:  # 404 just means "not started yet"
            st.warning(f"Status check failed: {exc.message} Retrying...")

    if workflow is not None and is_settled(workflow):
        state.proc_workflow = workflow
        state.proc_finished = True
        st.rerun()

    if elapsed > POLL_TIMEOUT_SECONDS:
        state.proc_workflow = workflow
        state.proc_note = "timeout"
        state.proc_finished = True
        st.rerun()

    if workflow is None:
        st.info("Queued: waiting for the workflow to start...")
    else:
        status = workflow.get("status")
        step = ", ".join(workflow.get("next_nodes") or []) or "finishing"
        st.info(
            f"Running. Status: "
            f"{get_status_label(status) if status else 'Processing'}. "
            f"Next step: {step}."
        )
    st.caption(f"Elapsed: {int(elapsed)}s (gives up after {POLL_TIMEOUT_SECONDS}s)")


def render_outcome():
    workflow = state.proc_workflow

    if workflow is None:
        st.warning(
            "No workflow state appeared within 2 minutes. The background "
            "task may have failed. Check the uvicorn terminal for "
            "'Invoice workflow failed'. The file was submitted once; "
            "do not resubmit unless the log confirms a failure."
        )
        st.button("Check again", on_click=recheck)
        return

    status = workflow.get("status")
    failed = bool(workflow.get("error_message") or workflow.get("error_stage"))

    if failed:
        state_text = "Failed"
    elif workflow.get("approval_required"):
        state_text = "Awaiting approval"
    else:
        state_text = "Completed"

    c1, c2, c3 = st.columns(3)
    c1.metric("Workflow status", get_status_label(status) if status else "-")
    c2.metric("Invoice ID", workflow.get("invoice_id") or "-")
    c3.metric("Workflow state", state_text)

    if workflow.get("submission_id"):
        ledger = str(workflow.get("submission_status") or "unknown").replace("_", " ")
        st.caption(f"Submission #{workflow['submission_id']} · ledger status: {ledger}")

    if failed:
        st.error(
            f"Stage: {workflow.get('error_stage') or 'unknown'}. "
            f"{workflow.get('error_message') or 'No message provided.'}"
        )
    elif workflow.get("approval_required"):
        st.warning(
            "The workflow is paused and needs a human decision. "
            "Open the Approval Center page from the sidebar to approve "
            "or reject this invoice."
        )
    elif workflow.get("review_case_id"):
        st.warning(
            f"This submission was routed to review case "
            f"{workflow['review_case_id']} (unknown vendor or duplicate). "
            "Open the Review Cases page from the sidebar to investigate."
        )
    elif status == "human_review":
        st.warning(
            "This invoice needs human review. It cannot be approved from "
            "the dashboard. See the Approval Center page for details."
        )
    else:
        st.success("Workflow completed.")

    st.button("Refresh status", on_click=recheck)

    invoice_id = workflow.get("invoice_id")

    if invoice_id:
        try:
            detail = api_client.get_invoice(invoice_id)
        except ApiError as exc:
            st.error(f"Could not load invoice details: {exc.message}")
        else:
            st.divider()
            st.subheader("Extraction and validation results")
            render_invoice_detail(detail, compact=True)
    elif not failed and not workflow.get("review_case_id"):
        st.info("No invoice record was created for this submission.")


if state.proc_thread_id:
    st.divider()
    st.subheader("Workflow")
    st.caption(f"File: {state.proc_filename}")

    if state.proc_notice:
        kind, text = state.proc_notice
        getattr(st, kind)(text)

    st.write("**Workflow ID**")
    st.code(state.proc_thread_id, language=None)

    if not state.proc_finished:
        st.fragment(run_every=POLL_SECONDS)(tracker)()
    else:
        render_outcome()