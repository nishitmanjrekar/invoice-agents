import streamlit as st
from api_client import ApiError, get_health


@st.cache_data(ttl=15)
def get_health_snapshot():
    return get_health()


def render_sidebar():
    with st.sidebar:
        st.title("Invoice Agent Console")
        st.caption("AI-powered invoice processing")

        try:
            health = get_health_snapshot()

            if health.get("status") == "healthy":
                st.success("Backend: Connected")
            else:
                st.warning("Backend: Degraded")

            records = health.get("records", {})
            st.caption(f"Database invoices: {records.get('invoices', 0)}")

        except ApiError as exc:
            st.error("Backend: Disconnected")
            st.caption(str(exc))


def render_banner():
    st.caption(
        "Local development dashboard. No authentication is enabled and "
        "no real payments are made."
    )