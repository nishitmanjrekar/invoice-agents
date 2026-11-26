import streamlit as st

from api_client import ApiError, get_health
from components.ui import esc


def render_brand():
    st.markdown(
        '<div class="ia-brand"><span class="ia-logo"></span><div>'
        '<div class="ia-brand-name">Invoice Agent</div>'
        '<div class="ia-brand-sub">Processing console</div>'
        "</div></div>",
        unsafe_allow_html=True,
    )


def render_navigation(pages):
    """
    Sidebar links built from the same role-filtered page list that
    st.navigation() received, so a role never sees a link it cannot open.
    """
    for page in pages:
        st.page_link(page)


def render_backend_status():
    """Backend/database status card. Uncached: health is read on every run."""
    try:
        health = get_health()

        if health.get("status") == "healthy":
            dot, title = "ok", "Backend: Connected"
        else:
            dot, title = "warn", "Backend: Degraded"

        records = health.get("records", {})
        sub = ""
        if "invoices" in records:
            sub = f'<div class="ia-side-sub">Database invoices: {esc(records["invoices"])}</div>'

        card = (
            '<div class="ia-side-card"><div class="ia-side-row">'
            f'<span class="ia-dot {dot}"></span>{title}</div>{sub}</div>'
        )

    except ApiError as exc:
        card = (
            '<div class="ia-side-card"><div class="ia-side-row">'
            '<span class="ia-dot bad"></span>Backend: Disconnected</div>'
            f'<div class="ia-side-sub">{esc(exc)}</div></div>'
        )

    st.markdown(card, unsafe_allow_html=True)


def render_sidebar(pages):
    with st.sidebar:
        render_brand()
        render_navigation(pages)
        st.divider()
        render_backend_status()


def render_user_card(username, role_label):
    initial = (username or "U")[:1].upper()
    st.markdown(
        f'<div class="ia-user"><div class="ia-avatar">{esc(initial)}</div><div>'
        f'<div class="ia-user-name">{esc(username)}</div>'
        f'<div class="ia-user-role">Role: {esc(role_label)}</div></div></div>',
        unsafe_allow_html=True,
    )


def render_banner():
    """Kept for compatibility. Page headers now carry their own subtitles."""
    return None
