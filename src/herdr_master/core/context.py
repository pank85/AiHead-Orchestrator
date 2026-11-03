"""Herdr environment and session resolution."""

import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel


class HerdrContext(BaseModel):
    is_inside_herdr: bool
    session_name: str
    socket_path: Path
    workspace_id: Optional[str] = None
    pane_id: Optional[str] = None
    tab_id: Optional[str] = None


def resolve_herdr_context(explicit_session: Optional[str] = None) -> HerdrContext:
    """Resolve the active Herdr session and socket path.

    Resolution precedence:
    1. If explicit_session is passed:
       - If 'default': ~/.config/herdr/herdr.sock
       - Else: ~/.config/herdr/sessions/<explicit_session>/herdr.sock
    2. If running inside an existing Herdr session (HERDR_SOCKET_PATH is set):
       - Use the existing socket and extract session name if nested under sessions/<name>.
    3. Fallback:
       - Always use 'default' (~/.config/herdr/herdr.sock).
    """
    env_socket = os.environ.get("HERDR_SOCKET_PATH")
    env_workspace = os.environ.get("HERDR_WORKSPACE_ID")
    env_pane = os.environ.get("HERDR_PANE_ID")
    env_tab = os.environ.get("HERDR_TAB_ID")
    is_inside = bool(os.environ.get("HERDR_ENV") == "1" or env_socket)

    if explicit_session:
        session_name = explicit_session
        if session_name == "default":
            socket_path = Path.home() / ".config" / "herdr" / "herdr.sock"
        else:
            socket_path = Path.home() / ".config" / "herdr" / "sessions" / session_name / "herdr.sock"
    elif env_socket:
        socket_path = Path(env_socket)
        # Determine session name from socket path
        if "/sessions/" in env_socket:
            try:
                session_name = env_socket.split("/sessions/")[1].split("/")[0]
            except IndexError:
                session_name = "default"
        else:
            session_name = "default"
    else:
        session_name = "default"
        socket_path = Path.home() / ".config" / "herdr" / "herdr.sock"

    return HerdrContext(
        is_inside_herdr=is_inside,
        session_name=session_name,
        socket_path=socket_path,
        workspace_id=env_workspace,
        pane_id=env_pane,
        tab_id=env_tab,
    )
