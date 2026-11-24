import json

import pandas as pd

from components.status import get_status_label


def fmt_timestamp(value):
    """ISO timestamp -> 'YYYY-MM-DD HH:MM:SS UTC' (blank if unparseable)."""
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    return "" if pd.isna(ts) else ts.strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_audit_details(details):
    """
    audit_logs.details is plain text for some events and a JSON string for
    others. Return {'previous', 'new', 'text'} for display.
    """
    result = {"previous": "", "new": "", "text": ""}

    if not details:
        return result

    try:
        data = json.loads(details)
    except (TypeError, ValueError):
        result["text"] = str(details)
        return result

    if not isinstance(data, dict):
        result["text"] = str(details)
        return result

    previous = data.get("previous_status")
    new = data.get("new_status")
    result["previous"] = get_status_label(previous) if previous else ""
    result["new"] = get_status_label(new) if new else ""

    rest = {
        key: value for key, value in data.items()
        if key not in ("previous_status", "new_status", "timestamp")
    }
    result["text"] = "; ".join(
        f"{key.replace('_', ' ')}: {value}" for key, value in rest.items()
    )
    return result