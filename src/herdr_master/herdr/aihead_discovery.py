"""Locate and focus an existing AiHead without changing its lifecycle."""

from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import Optional

from herdr_master.herdr.client import HerdrClient
from herdr_master.pipeline.ai_head import _resolve_agent_liveness


class AiHeadDiscoveryError(RuntimeError):
    """The snapshot does not identify a unique, focusable AiHead."""


@dataclass(frozen=True)
class AiHeadLocation:
    workspace_id: str
    tab_id: str
    pane_id: str


def _project_identity(cwd: str | Path) -> Path:
    """Resolve an absolute directory to its nearest project root, or itself."""
    if not isinstance(cwd, (str, Path)) or not str(cwd).strip() or "\x00" in str(cwd):
        raise AiHeadDiscoveryError("Unknown project identity: invalid cwd")
    path = Path(cwd)
    if not path.is_absolute():
        raise AiHeadDiscoveryError("Unknown project identity: cwd must be absolute")
    try:
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise AiHeadDiscoveryError("Unknown project identity: cwd is not a directory")
        for parent in (path, *path.parents):
            try:
                marker = (parent / ".herdr-master.toml").stat()
            except FileNotFoundError:
                continue
            if S_ISREG(marker.st_mode):
                return parent
        return path
    except (OSError, RuntimeError, ValueError) as exc:
        raise AiHeadDiscoveryError("Unknown project identity: cannot resolve cwd") from exc


async def discover_aihead(
    client: HerdrClient, *, workspace: Optional[str] = None,
    project: Optional[str | Path] = None,
) -> Optional[AiHeadLocation]:
    """Find a live AiHead in this client's session, optionally scoped by workspace ID or label.

    Returns None only when no candidate is present. Connection, protocol, and
    ambiguous or incomplete snapshot errors propagate to the caller. When project
    is provided, candidate cwd evidence must identify the same project; unknown
    or conflicting evidence raises instead of permitting focus or creation.
    """
    project_id = _project_identity(project) if project is not None else None
    snapshot = await client.get_snapshot()
    workspace_id = None
    if workspace is not None:
        matches = [w.workspace_id for w in snapshot.workspaces if w.workspace_id == workspace]
        if not matches:
            matches = [w.workspace_id for w in snapshot.workspaces if w.label == workspace]
        if len(matches) > 1:
            raise AiHeadDiscoveryError(f"Ambiguous workspace: {workspace}")
        if not matches:
            return None
        workspace_id = matches[0]

    tabs = {tab.tab_id: tab for tab in snapshot.tabs}
    candidates = []
    for pane in snapshot.panes:
        tab = tabs.get(pane.tab_id)
        pane_workspace = pane.workspace_id or (tab.workspace_id if tab else None)
        if workspace_id is not None and pane_workspace != workspace_id:
            continue
        alive, agent = _resolve_agent_liveness(pane, snapshot.agents)
        if not alive:
            continue
        is_named = agent is not None and (agent.name or "").strip() == "ai-head"
        is_labeled = (
            tab is not None
            and (tab.label or "").strip() == "ai-head"
            and not (agent and agent.name and agent.name.strip() != "ai-head")
        )
        if not is_named and not is_labeled:
            continue
        if project_id is not None:
            cwds = [cwd for cwd in (pane.cwd, agent.cwd if agent else None) if cwd is not None]
            if not cwds:
                raise AiHeadDiscoveryError(f"Unknown project identity for pane {pane.pane_id}")
            identities = {_project_identity(cwd) for cwd in cwds}
            if len(identities) != 1:
                raise AiHeadDiscoveryError(f"Conflicting project identity for pane {pane.pane_id}")
            if project_id not in identities:
                continue
        if (
            not tab
            or not pane_workspace
            or not tab.workspace_id
            or tab.workspace_id != pane_workspace
        ):
            raise AiHeadDiscoveryError(f"Incomplete location for pane {pane.pane_id}")
        location = AiHeadLocation(pane_workspace, tab.tab_id, pane.pane_id)
        candidates.append(location)

    if len(candidates) > 1:
        raise AiHeadDiscoveryError("Multiple live AiHead candidates found")
    return candidates[0] if candidates else None


async def focus_aihead(client: HerdrClient, location: AiHeadLocation) -> None:
    """Focus the discovered workspace, tab, and pane; propagate any RPC failure."""
    await client.focus_workspace(location.workspace_id)
    await client.focus_tab(location.tab_id)
    await client.focus_pane(location.pane_id)
