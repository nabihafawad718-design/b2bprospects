"""A deliberately strict Streamlit stand-in used only by the smoke test.

Signatures mirror the calls the app actually makes, so a typo or a wrong keyword
raises TypeError here rather than at runtime on the operator's machine. It does not
render anything and is never imported by the application.
"""
from __future__ import annotations

from contextlib import contextmanager


class _State(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


session_state = _State()


class _Secrets:
    def __getitem__(self, key):
        raise KeyError(key)

    def get(self, key, default=None):
        return default


secrets = _Secrets()

# Recorded output, useful for assertions.
rendered: list[tuple[str, str]] = []


class _Sink:
    """Context manager that proxies widget calls, used for containers and columns."""

    def __init__(self, label: str = "") -> None:
        self.label = label

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        return globals()[name]


class _Progress:
    def __init__(self, value: float = 0.0, text: str | None = None) -> None:
        self.value = value

    def progress(self, value: float, text: str | None = None) -> None:
        self.value = value

    def empty(self) -> None:
        self.value = 0.0


class _ColumnConfig:
    class TextColumn:
        def __init__(self, label, *, width=None, help=None):
            self.label = label

    class LinkColumn:
        def __init__(self, label, *, display_text=None, width=None):
            self.label = label


column_config = _ColumnConfig()


# ---------------------------------------------------------------------------
# Page / layout
# ---------------------------------------------------------------------------
def set_page_config(page_title=None, page_icon=None, layout=None, initial_sidebar_state=None):
    return None


def markdown(body, *, unsafe_allow_html=False):
    rendered.append(("markdown", str(body)))


def container(*, key=None, border=None):
    return _Sink(key or "")


def columns(spec, *, vertical_alignment=None, gap=None):
    count = spec if isinstance(spec, int) else len(spec)
    return [_Sink(f"col{i}") for i in range(count)]


@contextmanager
def expander(label, *, expanded=False):
    yield _Sink(label)


@contextmanager
def spinner(text=None, *, show_time=False):
    yield _Sink("spinner")


@contextmanager
def form(key, *, border=True, clear_on_submit=False):
    yield _Sink(key)


class _Sidebar(_Sink):
    def __init__(self):
        super().__init__("sidebar")


sidebar = _Sidebar()


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------
def text_input(label, *, value="", placeholder=None, key=None, type="default",
               help=None, label_visibility="visible", disabled=False, max_chars=None):
    return str(session_state.get(key, value)) if key else value


def number_input(label, *, min_value=None, max_value=None, value=None, step=None,
                 key=None, help=None, disabled=False, format=None):
    if key and key in session_state:
        return session_state[key]
    if value is not None:
        return value
    return min_value if min_value is not None else 0


def checkbox(label, *, value=False, key=None, help=None, disabled=False):
    if key and key in session_state:
        return bool(session_state[key])
    return value


def radio(label, options, *, index=0, key=None, horizontal=False,
          label_visibility="visible", help=None, disabled=False):
    if key and key in session_state:
        return session_state[key]
    return list(options)[index]


def selectbox(label, options, *, index=0, key=None, help=None, disabled=False,
              format_func=None, label_visibility="visible"):
    if key and key in session_state:
        return session_state[key]
    return list(options)[index]


def select_slider(label, *, options, value=None, key=None, help=None):
    return value if value is not None else list(options)[0]


def multiselect(label, options, *, default=None, key=None, format_func=None,
                help=None, label_visibility="visible"):
    if key and key in session_state:
        return list(session_state[key])
    return list(default or [])


def button(label, *, type="secondary", use_container_width=False, key=None,
           disabled=False, help=None):
    rendered.append(("button", str(label)))
    return False


def download_button(label, *, data, file_name=None, mime=None, key=None,
                    use_container_width=False, disabled=False, help=None):
    rendered.append(("download", str(label)))
    return False


def form_submit_button(label, *, type="secondary", use_container_width=False,
                       disabled=False):
    return False


def dataframe(data, *, use_container_width=False, hide_index=False, height=None,
              column_config=None, key=None):
    rendered.append(("dataframe", f"{len(data)} rows"))


def progress(value=0.0, *, text=None):
    return _Progress(value, text)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
def _message(body):
    rendered.append(("message", str(body)))


def error(body, *, icon=None):
    _message(body)


def warning(body, *, icon=None):
    _message(body)


def info(body, *, icon=None):
    _message(body)


def success(body, *, icon=None):
    _message(body)


def code(body, *, language=None):
    rendered.append(("code", str(body)[:200]))


def caption(body, *, unsafe_allow_html=False):
    _message(body)


def write(body, *, unsafe_allow_html=False):
    _message(body)


def exception(exc):
    rendered.append(("exception", repr(exc)))


def divider():
    return None


def stop():
    raise _Stop()


def rerun():
    return None


class _Stop(Exception):
    pass


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
def cache_data(*args, **kwargs):
    def decorator(func):
        return func
    if len(args) == 1 and callable(args[0]):
        return args[0]
    return decorator


def cache_resource(*args, **kwargs):
    def decorator(func):
        return func
    return decorator


def reset() -> None:
    session_state.clear()
    rendered.clear()
