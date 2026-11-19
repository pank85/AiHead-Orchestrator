"""Standalone plain-mode JSON-RPC client for herdr-core.

Each request owns one ``hc connect`` subprocess. The hc executable may start
its core if needed. Framed streams and event subscriptions are not supported.
"""

import asyncio
import json
from typing import Any


class CoreClientError(Exception):
    """A connection, timeout, or protocol failure."""


class CoreRPCError(CoreClientError):
    """An error response returned by herdr-core."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"herdr-core RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


async def request(
    method: str, params: dict[str, Any] | list[Any] | None = None, *,
    timeout: float = 5.0, executable: str = "hc",
) -> Any:
    """Send one JSON-RPC request through ``hc connect``.

    Callers must choose safe methods: the core API includes mutating operations.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method,
        "params": {} if params is None else params,
    }, allow_nan=False).encode() + b"\n"
    process = None

    async def exchange() -> Any:
        nonlocal process
        process = await asyncio.create_subprocess_exec(
            executable, "connect", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=16 * 1024 * 1024,
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(payload)
        await process.stdin.drain()
        while True:
            try:
                line = await process.stdout.readline()
            except ValueError as exc:
                raise CoreClientError("hc connect response exceeds size limit") from exc
            if not line:
                raise CoreClientError("hc connect closed before a matching response")
            try:
                response = json.loads(line)
            except (ValueError, UnicodeDecodeError) as exc:
                raise CoreClientError("invalid JSON from hc connect") from exc
            if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
                raise CoreClientError("invalid JSON-RPC message from hc connect")
            if "id" not in response:
                continue  # Notifications do not answer the request.
            if type(response["id"]) is not int or response["id"] != 1:
                continue
            if ("result" in response) == ("error" in response):
                raise CoreClientError("invalid JSON-RPC response")
            if "error" in response:
                error = response["error"]
                if (not isinstance(error, dict) or type(error.get("code")) is not int
                        or not isinstance(error.get("message"), str)):
                    raise CoreClientError("invalid JSON-RPC error")
                raise CoreRPCError(error["code"], error["message"], error.get("data"))
            return response["result"]

    try:
        return await asyncio.wait_for(exchange(), timeout)
    except asyncio.TimeoutError as exc:
        raise CoreClientError(f"hc connect request timed out after {timeout}s") from exc
    except OSError as exc:
        raise CoreClientError(f"hc connect failed: {exc}") from exc
    finally:
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()


async def terminal_list(*, timeout: float = 5.0, executable: str = "hc") -> Any:
    """Return the complete ``terminal.list`` result from herdr-core."""
    return await request("terminal.list", timeout=timeout, executable=executable)


async def terminal_run(
    argv: list[str],
    *,
    cwd: str | None = None,
    name: str | None = None,
    labels: dict[str, str] | None = None,
    parent: str | None = None,
    no_parent: bool = False,
    wait: bool = False,
    timeout: float = 5.0,
    executable: str = "hc",
) -> Any:
    """Spawn a terminal via ``terminal.run`` on herdr-core."""
    params: dict[str, Any] = {"argv": argv, "no_parent": no_parent, "wait": wait}
    if cwd is not None:
        params["cwd"] = cwd
    if name is not None:
        params["name"] = name
    if labels is not None:
        params["labels"] = labels
    if parent is not None:
        params["parent"] = parent
    return await request("terminal.run", params, timeout=timeout, executable=executable)


async def terminal_send(
    target: str,
    text: str,
    *,
    timeout: float = 5.0,
    executable: str = "hc",
) -> Any:
    """Send text to a terminal via ``terminal.send`` on herdr-core."""
    return await request(
        "terminal.send",
        {"terminal": target, "text": text},
        timeout=timeout,
        executable=executable,
    )


async def terminal_kill(
    target: str,
    *,
    timeout: float = 5.0,
    executable: str = "hc",
) -> Any:
    """Kill a terminal via ``terminal.kill`` on herdr-core."""
    return await request(
        "terminal.kill",
        {"terminal": target},
        timeout=timeout,
        executable=executable,
    )
