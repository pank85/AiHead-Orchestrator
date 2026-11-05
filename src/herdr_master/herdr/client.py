"""Typed client wrappers for Herdr Protocol 22 RPC and subscription APIs."""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from herdr_master.core.context import resolve_herdr_context
from herdr_master.herdr.protocol import parse_pane_read_result, parse_prompt_result
from herdr_master.herdr.snapshot import SessionSnapshot
from herdr_master.herdr.socket import HerdrConnectionError, HerdrRPCError, send_rpc
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

    async def focus_workspace(self, workspace_id: str) -> Dict[str, Any]:
        """Focus a specific workspace and bring it to foreground."""
        params = {"workspace_id": workspace_id}
        resp = await self._rpc("workspace.focus", params)
        return resp.get("result", {})

    async def focus_pane(self, pane_id: str) -> Dict[str, Any]:
        """Focus an existing pane (Protocol 22 pane.focus)."""
        resp = await self._rpc("pane.focus", {"pane_id": pane_id})
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

    async def focus_tab(self, tab_id: str) -> Dict[str, Any]:
        """Focus a specific tab within its workspace."""
        params = {"tab_id": tab_id}
        resp = await self._rpc("tab.focus", params)
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
        Retries automatically if the newly spawned shell has not yet initialized.
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

        last_err: Optional[Exception] = None
        for attempt in range(25):
            try:
                resp = await self._rpc("agent.start", params, timeout=rpc_timeout)
                return resp.get("result", {})
            except HerdrRPCError as err:
                last_err = err
                if err.code == "agent_pane_busy" or "not an available shell" in err.message:
                    if attempt < 24:
                        await asyncio.sleep(0.3)
                        continue
                raise
        if last_err:
            raise last_err
        return {}

    async def prompt_agent(
        self,
        target: str,
        prompt: str,
        wait: Optional[Any] = None,
        timeout_ms: Optional[int] = None,
        *,
        max_attempts: int = 20,
    ) -> Dict[str, Any]:
        """Send bracketed prompt text to an agent."""
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")

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

        last_err: Optional[Exception] = None
        for attempt in range(max_attempts):
            try:
                resp = await self._rpc("agent.prompt", params, timeout=rpc_timeout)
                result = resp.get("result")
                parse_prompt_result(result)
                return result
            except HerdrRPCError as err:
                last_err = err
                if attempt < max_attempts - 1 and (
                    err.code
                    in (
                        "agent_target_not_found",
                        "agent_prompt_stalled",
                        "agent_not_ready",
                        "agent_launch_pending",
                        "agent_busy",
                    )
                    or "does not have a detected agent" in err.message
                    or "not ready" in err.message
                ):
                    await asyncio.sleep(0.5)
                    continue
                raise
        if last_err:
            raise last_err
        return {}

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
        source: str = "recent",
    ) -> Dict[str, Any]:
        """Read a validated Protocol 22 scrollback record from the requested pane."""
        strip_ansi = False if format == "ansi" else True
        params: Dict[str, Any] = {
            "pane_id": pane_id,
            "source": source,
            "format": format,
            "strip_ansi": strip_ansi,
        }
        if lines is not None:
            params["lines"] = lines
        resp = await self._rpc("pane.read", params)
        result = resp.get("result")
        read = parse_pane_read_result(result)
        if read.pane_id != pane_id or read.source != source or read.format != format:
            raise HerdrConnectionError("pane.read response does not match request")
        # Preserve the existing unwrapped return shape, including extension fields.
        return result["read"]

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

    async def pane_send_text(
        self,
        pane_id: str,
        text: str,
    ) -> Dict[str, Any]:
        """Send raw text characters directly to a pane via Protocol 22."""
        params = {"pane_id": pane_id, "text": text}
        resp = await self._rpc("pane.send_text", params)
        return resp.get("result", {})

    async def pane_send_keys(
        self,
        pane_id: str,
        keys: Union[str, List[str]],
    ) -> Dict[str, Any]:
        """Send raw keystrokes directly to a pane via Protocol 22."""
        key_list = [keys] if isinstance(keys, str) else list(keys)
        params = {"pane_id": pane_id, "keys": key_list}
        resp = await self._rpc("pane.send_keys", params)
        return resp.get("result", {})

    async def pane_send_input(
        self,
        pane_id: str,
        text: Optional[str] = None,
        keys: Optional[Union[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """Send raw text or keystrokes directly to a pane via Protocol 22."""
        params: Dict[str, Any] = {"pane_id": pane_id}
        if text is not None:
            params["text"] = text
        if keys is not None:
            params["keys"] = [keys] if isinstance(keys, str) else list(keys)
        resp = await self._rpc("pane.send_input", params)
        return resp.get("result", {})

    def subscribe(self, subscriptions: List[Dict[str, Any]]) -> HerdrSubscription:
        """Create a new events.subscribe connection with the specified subscriptions."""
        return HerdrSubscription(
            socket_path=self.socket_path,
            subscriptions=subscriptions,
            timeout=self.timeout,
        )
