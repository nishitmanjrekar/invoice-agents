import streamlit as st

st.set_page_config(
    page_title="Invoice Agent Console",
    page_icon=":material/receipt_long:",
    layout="wide",
)

from api_client import ApiError, get_current_user, login  # noqa: E402
from components.layout import render_sidebar, render_user_card  # noqa: E402
from components.theme import inject_theme  # noqa: E402

inject_theme()


def render_login():
    # No session yet: hide the sidebar so no page links show before sign-in.
    st.markdown(
        "<style>[data-testid='stSidebar'],"
        "[data-testid='stSidebarCollapsedControl']{display:none}</style>",
        unsafe_allow_html=True,
    )
    shell = st.container(key="login_shell")

    with shell:
        left, right = st.columns([1, 1.05], gap="small")

        with left.container(key="login_left"):
            st.markdown(
                '<span class="ia-logo" style="display:block"></span>'
                '<div class="ia-login-name">Invoice Agent Console</div>'
                '<p class="ia-login-lead">Review, validate and approve supplier '
                "invoices in one secure workspace.</p>"
                '<ul class="ia-login-points">'
                "<li>Extracts vendor, totals and line items from each invoice</li>"
                "<li>Checks vendors, purchase orders and duplicates automatically</li>"
                "<li>Sends exceptions to a person before anything is approved</li>"
                "</ul>",
                unsafe_allow_html=True,
            )

        with right.container(key="login_right"):
            st.markdown(
                '<h2 class="ia-login-title">Sign in</h2>'
                '<p class="ia-login-sub">Use your Invoice Agent account to continue.</p>',
                unsafe_allow_html=True,
            )

            with st.form("login_form"):
                username = st.text_input("Username", placeholder="Enter your username")
                password = st.text_input(
                    "Password", type="password", placeholder="Enter your password"
                )
                submitted = st.form_submit_button(
                    "Sign in",
                    type="primary",
                    width="stretch",
                )

            if submitted:
                if not username.strip() or not password:
                    st.error("Enter your username and password.")
                    return

                try:
                    result = login(username.strip(), password)
                    token = result.get("access_token")
                    user = result.get("user")

                    if not token or not user:
                        st.error("The API returned an incomplete login response.")
                        return

                    st.session_state["access_token"] = token
                    st.session_state["user"] = user
                    st.rerun()

                except ApiError as exc:
                    st.error(f"Login failed: {exc}")


# Validate an existing session against the backend.
token = st.session_state.get("access_token")

if token:
    try:
        identity = get_current_user()
        st.session_state["user"] = identity.get("user")
    except ApiError as exc:
        if exc.status_code in (401, 403):
            st.session_state.pop("access_token", None)
            st.session_state.pop("user", None)
        else:
            st.error(f"Unable to verify your session: {exc}")
            st.stop()

if not st.session_state.get("access_token"):
    render_login()
    st.stop()

user = st.session_state.get("user") or {}
role = user.get("role")

if role not in ("admin", "finance_manager", "reviewer", "uploader"):
    st.error("Your account has an unsupported role.")
    st.stop()


# Staff pages are available only to staff roles.
pages = [
    st.Page(
        "pages/1_Dashboard.py",
        title="Dashboard",
        icon=":material/dashboard:",
        default=True,
    ),
    st.Page(
        "pages/2_Invoice_Processing.py",
        title="Invoice Processing",
        icon=":material/upload_file:",
    ),
    st.Page(
        "pages/3_Invoice_Management.py",
        title="Invoice Management",
        icon=":material/table_view:",
    ),
]

if role in ("admin", "finance_manager", "reviewer"):
    pages.extend([
        st.Page(
            "pages/4_Approval_Center.py",
            title="Approval Center",
            icon=":material/fact_check:",
        ),
        st.Page(
            "pages/5_Review_Cases.py",
            title="Review Cases",
            icon=":material/policy:",
        ),
        st.Page(
            "pages/6_Audit_Logs.py",
            title="Audit Logs",
            icon=":material/history:",
        ),
    ])

# The built-in menu is hidden; render_sidebar() draws the same page list.
navigation = st.navigation(pages, position="hidden")

render_sidebar(pages)

with st.sidebar:
    st.divider()
    render_user_card(user.get("username", "User"), role.replace("_", " ").title())

    if st.button("Log out", width="stretch", icon=":material/logout:"):
        st.session_state.pop("access_token", None)
        st.session_state.pop("user", None)
        st.cache_data.clear()
        st.rerun()

navigation.run()
