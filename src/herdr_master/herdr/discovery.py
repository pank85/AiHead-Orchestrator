"""Read-only local session discovery for the workspace control panel."""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.snapshot import SessionSnapshot


class SessionInventory(BaseModel):
    """Last snapshot and connection health for one local Herdr socket."""

    session_name: str
    socket_path: Path
    snapshot: Optional[SessionSnapshot] = None
    error: Optional[str] = None


def discover_clients(primary: HerdrClient, config_dir: Optional[Path] = None) -> list[HerdrClient]:
    """Include the current socket and existing default/named session sockets.

    Socket existence is not liveness: callers must fetch snapshots and surface failures.
    Never create a session just to discover it.
    """
    config = config_dir or Path.home() / ".config" / "herdr"
    clients = {Path(primary.socket_path): primary}
    candidates = [("default", config / "herdr.sock")]
    try:
        candidates.extend(
            (path.parent.name, path) for path in sorted((config / "sessions").glob("*/herdr.sock"))
        )
        for name, path in candidates:
            if path.is_socket() and path not in clients:
                clients[path] = HerdrClient(session=name, socket_path=path, timeout=2.0)
    except OSError:
        # An inaccessible session directory must not prevent the current socket from working.
        pass
    return list(clients.values())
