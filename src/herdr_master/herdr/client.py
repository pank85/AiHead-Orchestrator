"""Typed client wrappers for Herdr Protocol 22 RPC and subscription APIs."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from herdr_master.core.context import resolve_herdr_context
from herdr_master.herdr.snapshot import SessionSnapshot
from herdr_master.herdr.socket import send_rpc
from herdr_master.herdr.subscribe import HerdrSubscription


class HerdrClient:
    """Client for interacting with Herdr over Unix domain socket (Protocol 22)."""

    def __init__(
        self,
        session: Optional[str] = None,
        socket_path: Optional[Union[str, Path]] = None,
        timeout: float = 10.0,
    ) -> None:
        self.session = session
        if socket_path is not None:
            self.socket_path = Path(socket_path)
        else:
            ctx = resolve_herdr_context(explicit_session=session)
            self.socket_path = ctx.socket_path
        self.timeout = timeout

    async def _rpc(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute one-shot RPC and return raw response dict."""
        rpc_timeout = timeout if timeout is not None else self.timeout
        return await send_rpc(
            socket_path=self.socket_path,
            method=method,
            params=params,
            timeout=rpc_timeout,
        )

    async def ping(self) -> Dict[str, Any]:
        """Check socket server liveness and capabilities."""
        resp = await self._rpc("ping", {})
        return resp.get("result", {})

    async def get_snapshot(self) -> SessionSnapshot:
        """Fetch full session snapshot and validate Protocol 22."""
        resp = await self._rpc("session.snapshot", {})
        return SessionSnapshot.from_response(resp)

    async def create_workspace(
        self,
        label: str,
        focus: bool = False,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new Herdr workspace."""
        params: Dict[str, Any] = {"label": label, "focus": focus}
        if cwd:
            params["cwd"] = cwd
        resp = await self._rpc("workspace.create", params)
        return resp.get("result", {})

    async def close_workspace(
        self,
        workspace_id: str,
        close_group: bool = False,
    ) -> Dict[str, Any]:
        """Close an existing workspace."""
        params = {"workspace_id": workspace_id, "close_group": close_group}
        resp = await self._rpc("workspace.close", params)
        return resp.get("result", {})

    async def create_tab(
        self,
        workspace_id: Optional[str] = None,
        label: Optional[str] = None,
        focus: bool = False,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new tab in the specified or focused workspace."""
        params: Dict[str, Any] = {"focus": focus}
        if workspace_id:
            params["workspace_id"] = workspace_id
        if label:
            params["label"] = label
        if cwd:
            params["cwd"] = cwd
        resp = await self._rpc("tab.create", params)
        return resp.get("result", {})

    async def close_tab(self, tab_id: str) -> Dict[str, Any]:
        """Close an existing tab."""
        params = {"tab_id": tab_id}
        resp = await self._rpc("tab.close", params)
        return resp.get("result", {})

    async def start_agent(
        self,
        name: str,
        kind: str,
        pane_id: str,
        args: Optional[List[str]] = None,
        timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        """Start a supported interactive agent in an existing pane.

        timeout_ms must be in (3000, 300000].
        """
        clamped_timeout = max(3001, min(300000, timeout_ms))
        params: Dict[str, Any] = {
            "name": name,
            "kind": kind,
            "pane_id": pane_id,
            "args": args if args is not None else [],
            "timeout_ms": clamped_timeout,
        }
        rpc_timeout = (clamped_timeout / 1000.0) + 15.0
        resp = await self._rpc("agent.start", params, timeout=rpc_timeout)
        return resp.get("result", {})

    async def prompt_agent(
        self,
        target: str,
        prompt: str,
        wait: Optional[Any] = None,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send bracketed prompt text to an agent."""
        params: Dict[str, Any] = {
            "target": target,
            "text": prompt,
        }
        if wait is not None:
            params["wait"] = wait
        if timeout_ms is not None:
            params["timeout_ms"] = timeout_ms
            rpc_timeout = (timeout_ms / 1000.0) + 15.0
        else:
            rpc_timeout = 30.0
        resp = await self._rpc("agent.prompt", params, timeout=rpc_timeout)
        return resp.get("result", {})

    async def read_agent(
        self,
        agent_or_pane: str,
        format: str = "ansi",
        lines: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read output from an agent."""
        strip_ansi = False if format == "ansi" else True
        params: Dict[str, Any] = {
            "target": agent_or_pane,
            "format": format,
            "strip_ansi": strip_ansi,
        }
        if lines is not None:
            params["lines"] = lines
        resp = await self._rpc("agent.read", params)
        return resp.get("result", {})

    async def read_pane(
        self,
        pane_id: str,
        format: str = "ansi",
        lines: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read scrollback directly from a pane."""
        strip_ansi = False if format == "ansi" else True
        params: Dict[str, Any] = {
            "pane_id": pane_id,
            "format": format,
            "strip_ansi": strip_ansi,
        }
        if lines is not None:
            params["lines"] = lines
        resp = await self._rpc("pane.read", params)
        return resp.get("result", {})

    async def get_agent(self, agent_name_or_pane: str) -> Dict[str, Any]:
        """Get agent details."""
        params = {"target": agent_name_or_pane}
        resp = await self._rpc("agent.get", params)
        return resp.get("result", {})

    async def list_agents(self) -> List[Dict[str, Any]]:
        """List all active agents across sessions."""
        resp = await self._rpc("agent.list", {})
        result = resp.get("result", {})
        if isinstance(result, list):
            return result
        return result.get("agents", [])

    async def send_keys(
        self,
        pane_id: str,
        keys: Union[str, List[str]],
    ) -> Dict[str, Any]:
        """Send authorized raw keystrokes to a pane."""
        key_list = [keys] if isinstance(keys, str) else list(keys)
        params = {"target": pane_id, "keys": key_list}
        resp = await self._rpc("agent.send_keys", params)
        return resp.get("result", {})

    def subscribe(self, subscriptions: List[Dict[str, Any]]) -> HerdrSubscription:
        """Create a new events.subscribe connection with the specified subscriptions."""
        return HerdrSubscription(
            socket_path=self.socket_path,
            subscriptions=subscriptions,
            timeout=self.timeout,
        )
