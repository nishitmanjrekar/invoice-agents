import json

import pandas as pd
import streamlit as st

from components.status import risk_tone, style_tone_columns
from components.ui import kpi_card

CHECK_LABELS = {
    "vendor": "Vendor",
    "purchase_order": "Purchase order",
    "po_amount": "PO amount",
    "line_items": "Line items",
    "duplicate": "Duplicate",
    "security": "Security",
}

# Keys every check carries; everything else is shown as extra detail.
_CORE_KEYS = ("passed", "issue", "message")


def parse_report(report):
    """Accept a dict or a JSON string; return a dict (empty if unusable)."""
    if isinstance(report, dict):
        return report
    if isinstance(report, str) and report.strip():
        try:
            data = json.loads(report)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
    return {}


def _flatten(value):
    if isinstance(value, dict):
        return ", ".join(
            f"{k.replace('_', ' ')} {_flatten(v)}" for k, v in value.items()
        )
    if isinstance(value, list):
        return ", ".join(_flatten(v) for v in value) if value else "none"
    if value is None:
        return "none"
    return str(value)


def _result(check):
    passed = check.get("passed") if isinstance(check, dict) else None
    if isinstance(passed, bool):
        return "Pass" if passed else "Flagged"
    return ""


def _details(check):
    if not isinstance(check, dict):
        return _flatten(check)

    parts = []
    if check.get("message"):
        parts.append(str(check["message"]))
    if check.get("issue"):
        parts.append(f"Issue: {check['issue']}")

    extras = {k: v for k, v in check.items() if k not in _CORE_KEYS}

    # Nested purchase order record -> compact one-liner.
    po = extras.pop("purchase_order", None)
    if isinstance(po, dict):
        parts.append(
            f"{po.get('po_number', '')} "
            f"({po.get('currency', '')} {po.get('po_amount', '')})".strip()
        )

    if extras:
        parts.append(_flatten(extras))

    return " | ".join(p for p in parts if p)


def summarize_checks(report):
    """Return a list of {Check, Result, Details} rows from report['checks']."""
    checks = report.get("checks")
    if not isinstance(checks, dict):
        return []

    return [
        {
            "Check": CHECK_LABELS.get(name, str(name).replace("_", " ").title()),
            "Result": _result(check),
            "Details": _details(check),
        }
        for name, check in checks.items()
    ]


def _result_tone(value):
    return {"Pass": "success", "Flagged": "danger"}.get(value, "neutral")


def render_validation_report(report, show_raw=True):
    """Shared validation view for the Approval Center and Review Cases."""
    data = parse_report(report)

    if not data:
        st.info("No validation report is available for this item.")
        return

    c1, c2, c3 = st.columns(3)

    risk = data.get("risk_level")
    with c1:
        kpi_card(
            "Risk level",
            str(risk).title() if risk else "-",
            tone=risk_tone(risk),
            small=True,
        )

    needs_review = data.get("requires_human_review")
    with c2:
        kpi_card(
            "Human review required",
            "-" if needs_review is None else ("Yes" if needs_review else "No"),
            tone="warning" if needs_review else "neutral",
            small=True,
        )

    route = data.get("route")
    with c3:
        kpi_card(
            "Suggested route",
            str(route).replace("_", " ").title() if route else "-",
            small=True,
        )

    rows = summarize_checks(data)
    if rows:
        table = pd.DataFrame(rows)
        st.dataframe(
            style_tone_columns(table, {"Result": _result_tone}),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("This report has no itemised checks.")

    issues = data.get("issues") or []
    if issues:
        st.warning("Issues found:\n\n" + "\n".join(f"- {_flatten(i)}" for i in issues))
    else:
        st.success("No validation issues were reported.")

    if show_raw:
        with st.expander("Raw validation report"):
            st.json(data)
