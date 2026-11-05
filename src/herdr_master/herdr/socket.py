"""Async Unix domain socket connection, newline-delimited JSON framing, and one-shot RPC."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class HerdrConnectionError(Exception):
    """Raised when connection to Herdr socket fails or times out."""

    pass


class HerdrRPCError(Exception):
    """Raised when Herdr returns an RPC error response."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Herdr RPC error [{code}]: {message}")


class NDJSONBuffer:
    """Line buffer that reassembles chunked byte streams into complete JSON objects."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> List[Dict[str, Any]]:
        """Feed bytes into the buffer and return any completely framed JSON objects."""
        self._buffer.extend(data)
        messages: List[Dict[str, Any]] = []

        while b"\n" in self._buffer:
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue
            try:
                parsed = json.loads(line_str)
                if isinstance(parsed, dict):
                    messages.append(parsed)
            except json.JSONDecodeError:
                # Discard corrupted line or log warning
                continue

        return messages

    def clear(self) -> None:
        """Clear the internal buffer."""
        self._buffer.clear()

    @property
    def remaining(self) -> bytes:
        """Return unparsed bytes currently waiting in the buffer."""
        return bytes(self._buffer)


def validate_rpc_response(response: Dict[str, Any], request_id: str) -> None:
    """Require a matching Protocol 22 response envelope, or raise an error."""
    if response.get("id") != request_id:
        raise HerdrConnectionError("Invalid RPC response: mismatched request id")
    err = response.get("error")
    if err is not None:
        if (
            not isinstance(err, dict)
            or not isinstance(err.get("code"), str)
            or not err["code"]
            or not isinstance(err.get("message"), str)
            or response.get("result") is not None
        ):
            raise HerdrConnectionError("Invalid RPC response: malformed error envelope")
        raise HerdrRPCError(code=err["code"], message=err["message"])
    if "result" not in response:
        raise HerdrConnectionError("Invalid RPC response: missing result or error")


async def send_rpc(
    socket_path: Union[str, Path],
    method: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
    req_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a one-shot RPC request against the Herdr Unix domain socket.

    Herdr closes RPC sockets after a single response. Do not pool.
    """
    path_obj = Path(socket_path)
    if not path_obj.exists():
        raise HerdrConnectionError(f"Herdr socket does not exist at {path_obj}")

    request_id = req_id or f"hm-{uuid.uuid4().hex[:8]}"
    request_payload = {
        "id": request_id,
        "method": method,
        "params": params if params is not None else {},
    }
    encoded = json.dumps(request_payload).encode("utf-8") + b"\n"

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(path_obj)),
            timeout=timeout,
        )
    except (FileNotFoundError, ConnectionRefusedError, PermissionError) as exc:
        raise HerdrConnectionError(f"Failed to connect to Herdr socket {path_obj}: {exc}") from exc
    except asyncio.TimeoutError as exc:
        raise HerdrConnectionError(f"Connection timeout to Herdr socket {path_obj}") from exc

    try:
        writer.write(encoded)
        await writer.drain()

        # Line reassembly for response
        buffer = NDJSONBuffer()
        response: Optional[Dict[str, Any]] = None

        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            if not chunk:
                break
            messages = buffer.feed(chunk)
            if messages:
                response = messages[0]
                break

        if response is None:
            raise HerdrConnectionError("Connection closed before response received")

        # Neither success nor rejection is evidence unless it answers this request.
        validate_rpc_response(response, request_id)
        return response

    except asyncio.TimeoutError as exc:
        raise HerdrConnectionError(
            f"RPC timeout waiting for response to '{method}' after {timeout}s"
        ) from exc
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
