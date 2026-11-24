from urllib.parse import quote

import requests
from config import API_BASE_URL, PAGE_SIZE, REQUEST_TIMEOUT, UPLOAD_TIMEOUT


class ApiError(Exception):
    """Raised when an API request fails."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self):
        return self.message


def _request(method, endpoint, *, params=None, json=None, files=None,
             timeout=REQUEST_TIMEOUT):
    url = f"{API_BASE_URL}{endpoint}"

    try:
        response = requests.request(
            method, url, params=params, json=json, files=files,
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError:
        raise ApiError(
            f"Cannot connect to the API at {API_BASE_URL}. "
            "Check whether the FastAPI backend is running."
        )
    except requests.exceptions.Timeout:
        raise ApiError("The API request timed out.")
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"API request failed: {exc}")

    if not response.ok:
        detail = None
        try:
            detail = response.json().get("detail")
        except Exception:
            pass
        if isinstance(detail, list):
            detail = "; ".join(str(item.get("msg", item)) for item in detail)
        raise ApiError(
            detail or f"API returned HTTP {response.status_code}.",
            status_code=response.status_code,
        )

    try:
        return response.json()
    except ValueError:
        raise ApiError("The API returned invalid JSON.")


def api_get(endpoint, params=None):
    return _request("GET", endpoint, params=params)


# ------------------------------------------------------------------ reads
def get_health():
    return api_get("/health")


def get_invoices():
    """Return ALL invoices by paging through /invoices (backend max 200)."""
    invoices = []
    offset = 0

    while True:
        page = api_get("/invoices", {"limit": PAGE_SIZE, "offset": offset})
        batch = page.get("invoices", [])
        invoices.extend(batch)
        offset += PAGE_SIZE

        if not batch or len(invoices) >= page.get("total", 0):
            break

    return invoices


def get_invoice(invoice_id):
    return api_get(f"/invoices/{int(invoice_id)}")


def get_review_cases(status="open"):
    """status: 'open', 'rejected' or 'all'."""
    result = api_get("/review-cases", {"status": status})
    return result.get("review_cases", [])


def get_workflow(thread_id):
    """GET /workflows/{thread_id}. A 404 means 'not started yet'."""
    return api_get(f"/workflows/{quote(str(thread_id), safe='')}")


def list_workflows(limit=200):
    """
    GET /workflows: saved workflows, newest first. This is the only
    source of invoice_id -> thread_id links. Thread IDs are never guessed.
    """
    result = api_get("/workflows", {"limit": limit})
    return result.get("workflows", [])


def upload_invoice(filename, content: bytes, resubmit_of=None):
    """
    POST /invoices/process. Called exactly once per submission.
    resubmit_of: id of an earlier submission of the same file to resubmit explicitly.
    """
    params = {"resubmit_of": int(resubmit_of)} if resubmit_of is not None else None

    return _request(
        "POST",
        "/invoices/process",
        params=params,
        files={"file": (filename, content, "text/plain")},
        timeout=UPLOAD_TIMEOUT,
    )


def submit_decision(thread_id, action, reason=""):
    """
    POST /workflows/{thread_id}/decision. Resumes the paused workflow.
    action is 'approve' or 'reject'; a reject needs a non-empty reason.
    """
    if action not in ("approve", "reject"):
        raise ApiError("Decision must be 'approve' or 'reject'.")

    reason = (reason or "").strip()
    if action == "reject" and not reason:
        raise ApiError("A rejection reason is required.")

    return _request(
        "POST",
        f"/workflows/{quote(str(thread_id), safe='')}/decision",
        json={"action": action, "reason": reason},
        timeout=UPLOAD_TIMEOUT,
    )


def reject_review_case(case_id, reason):
    """
    POST /review-cases/{case_id}/reject. Rejection only: the backend does
    not allow approving unknown-vendor or duplicate cases.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ApiError("A rejection reason is required.")

    return _request(
        "POST",
        f"/review-cases/{int(case_id)}/reject",
        json={"action": "reject", "reason": reason},
        timeout=REQUEST_TIMEOUT,
    )