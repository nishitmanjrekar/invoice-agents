"""Small presentation helpers shared by every page.

These functions only render. They never call the API and never touch
authentication state. All dynamic text is escaped before it reaches HTML,
because invoice fields come from uploaded files.
"""
import html

import streamlit as st

from components.status import get_status_label, status_tone


def esc(value):
    return html.escape("" if value is None else str(value))


def html_block(markup):
    """Render trusted markup. Lines are joined so Markdown never sees indentation."""
    st.markdown(
        "".join(line.strip() for line in markup.strip().splitlines()),
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------ headers
def page_header(title, subtitle=""):
    """Render the page title and return a right-hand column for actions."""
    left, right = st.columns([5, 1.6], vertical_alignment="center")
    sub = f"<p>{esc(subtitle)}</p>" if subtitle else ""
    left.markdown(
        f'<div class="ia-page-head"><h1>{esc(title)}</h1>{sub}</div>',
        unsafe_allow_html=True,
    )
    return right


def section_title(title, caption=""):
    sub = f'<div class="ia-section-sub">{esc(caption)}</div>' if caption else ""
    st.markdown(
        f'<div class="ia-section-title">{esc(title)}</div>{sub}',
        unsafe_allow_html=True,
    )


# -------------------------------------------------------------------- tiles
def kpi_card(label, value, hint="", tone="neutral", small=False):
    size = " ia-kpi-sm" if small else ""
    st.markdown(
        '<div class="ia-kpi">'
        f'<div class="ia-kpi-label"><span class="ia-dot ia-{esc(tone)}"></span>{esc(label)}</div>'
        f'<div class="ia-kpi-value{size}">{esc(value)}</div>'
        f'<div class="ia-kpi-hint">{esc(hint)}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def pill(text, tone="neutral"):
    return f'<span class="ia-pill ia-{esc(tone)}">{esc(text)}</span>'


def status_pill(status):
    return pill(get_status_label(status), status_tone(status))


def field_grid(fields, numeric=()):
    """fields: list of (label, value). `numeric` lists labels set in tabular figures."""
    cells = []
    for label, value in fields:
        shown = esc(value) if value not in (None, "") else "&ndash;"
        cls = " ia-num" if label in numeric else ""
        cells.append(
            f'<div><div class="ia-field-label">{esc(label)}</div>'
            f'<div class="ia-field-value{cls}">{shown}</div></div>'
        )
    st.markdown(
        f'<div class="ia-fields">{"".join(cells)}</div>', unsafe_allow_html=True
    )


def empty_state(title, body=""):
    detail = f'<div class="ia-empty-body">{esc(body)}</div>' if body else ""
    st.markdown(
        f'<div class="ia-empty"><div class="ia-empty-title">{esc(title)}</div>{detail}</div>',
        unsafe_allow_html=True,
    )


def stepper(steps):
    """
    steps: list of (name, state, note) where state is one of
    done, active, pending, success, warning, danger.
    """
    parts = []
    for name, state, note in steps:
        note_html = f'<div class="ia-step-note">{esc(note)}</div>' if note else ""
        parts.append(
            f'<div class="ia-step ia-{esc(state)}"><div class="ia-step-bar"></div>'
            f'<div class="ia-step-name">{esc(name)}</div>{note_html}</div>'
        )
    st.markdown(f'<div class="ia-steps">{"".join(parts)}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------ table helpers
def money(currency, amount):
    try:
        return f"{currency or ''} {float(amount):,.2f}".strip()
    except (TypeError, ValueError):
        return "" if amount is None else str(amount)


def paginate(total, key, default_size=25, sizes=(10, 25, 50, 100)):
    """
    Pager controls. Returns (start, end) row bounds for the current page.
    Page position lives in session state under `key`; callers pass a `key`
    that does not collide with any other widget.
    """
    size_key, page_key = f"{key}_size", f"{key}_page"
    st.session_state.setdefault(size_key, default_size)
    st.session_state.setdefault(page_key, 1)

    size = st.session_state[size_key]
    pages = max(1, -(-total // size))
    page = min(max(1, st.session_state[page_key]), pages)
    st.session_state[page_key] = page

    c_info, c_size, c_prev, c_page, c_next = st.columns(
        [3, 1.5, 1.2, 1.3, 1.2], vertical_alignment="center"
    )
    start = (page - 1) * size
    end = min(start + size, total)
    c_info.markdown(
        f'<div class="ia-note">Showing {start + 1 if total else 0}&ndash;{end} of {total}</div>',
        unsafe_allow_html=True,
    )

    def _size_changed():
        st.session_state[page_key] = 1

    c_size.selectbox(
        "Rows per page",
        list(sizes),
        key=size_key,
        on_change=_size_changed,
        format_func=lambda n: f"{n} / page",
        label_visibility="collapsed",
    )

    def _step(delta):
        st.session_state[page_key] = min(max(1, page + delta), pages)

    c_prev.button(
        "Prev", key=f"{key}_prev", on_click=_step, args=(-1,),
        disabled=page <= 1, icon=":material/chevron_left:", width="stretch",
    )
    c_page.markdown(
        f'<div class="ia-note" style="text-align:center">Page {page} of {pages}</div>',
        unsafe_allow_html=True,
    )
    c_next.button(
        "Next", key=f"{key}_next", on_click=_step, args=(1,),
        disabled=page >= pages, icon=":material/chevron_right:", width="stretch",
    )
    return start, end


def page_link_if_allowed(path, label, icon):
    """
    Link to another page only when it is registered for this role.
    Navigation is built from the role-filtered page list in app.py, so a link
    to an unregistered page is simply not shown.
    """
    try:
        st.page_link(path, label=label, icon=icon, width="stretch")
        return True
    except Exception:
        return False
