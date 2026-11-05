"""Long-lived events.subscribe connection for Herdr Protocol 22."""

import asyncio
import json
import uuid
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from herdr_master.herdr.socket import HerdrConnectionError, NDJSONBuffer, validate_rpc_response


class HerdrSubscription:
    """Long-lived events.subscribe connection to Herdr socket.

    Herdr invariants:
    - events.subscribe stays open and streams until closed.
    - A second events.subscribe on that same socket is rejected/ignored.
    - Any subscription-set change closes the socket and opens a new one with the full set.
    """

    def __init__(
        self,
        socket_path: Union[str, Path],
        subscriptions: List[Dict[str, Any]],
        timeout: float = 10.0,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.subscriptions = subscriptions
        self.timeout = timeout
        self._subscribed = False
        self._closed = False
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._buffer = NDJSONBuffer()
        self._pending_events: deque[Dict[str, Any]] = deque()

    async def connect(self) -> Dict[str, Any]:
        """Establish connection and initiate events.subscribe."""
        if self._subscribed:
            raise RuntimeError("Socket already subscribed. Reconnect to change subscriptions.")
        if self._closed:
            raise RuntimeError("Subscription is closed. Reconnect to change subscriptions.")

        if not self.socket_path.exists():
            raise HerdrConnectionError(f"Herdr socket does not exist at {self.socket_path}")

        req_id = f"sub-{uuid.uuid4().hex[:8]}"
        payload = {
            "id": req_id,
            "method": "events.subscribe",
            "params": {"subscriptions": self.subscriptions},
        }
        encoded = json.dumps(payload).encode("utf-8") + b"\n"

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)),
                timeout=self.timeout,
            )
        except (FileNotFoundError, ConnectionRefusedError, PermissionError) as exc:
            raise HerdrConnectionError(
                f"Failed to connect to Herdr socket {self.socket_path}: {exc}"
            ) from exc
        except asyncio.TimeoutError as exc:
            raise HerdrConnectionError(
                f"Connection timeout to Herdr socket {self.socket_path}"
            ) from exc

        try:
            self._writer.write(encoded)
            await self._writer.drain()

            # Read confirmation frame
            start_frame: Optional[Dict[str, Any]] = None
            while True:
                chunk = await asyncio.wait_for(self._reader.read(4096), timeout=self.timeout)
                if not chunk:
                    break
                messages = self._buffer.feed(chunk)
                if messages:
                    start_frame = messages.pop(0)
                    if messages:
                        self._pending_events.extend(messages)
                    break

            if start_frame is None:
                await self.close()
                raise HerdrConnectionError("Connection closed before subscription was acknowledged")

            validate_rpc_response(start_frame, req_id)
            result = start_frame.get("result")
            if not isinstance(result, dict) or result.get("type") != "subscription_started":
                raise HerdrConnectionError("Invalid subscription acknowledgement")

            self._subscribed = True
            return start_frame

        except asyncio.CancelledError:
            await self.close()
            raise
        except asyncio.TimeoutError as exc:
            await self.close()
            raise HerdrConnectionError("Timeout awaiting subscription acknowledgement") from exc
        except Exception:
            await self.close()
            raise

    async def subscribe(self, *args: Any, **kwargs: Any) -> None:
        """Reject second subscribe on active connection."""
        if self._subscribed:
            raise RuntimeError("Socket already subscribed. Reconnect to change subscriptions.")
        if self._closed:
            raise RuntimeError("Subscription is closed. Reconnect to change subscriptions.")
        await self.connect()

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        """Stream parsed events line by line as they arrive."""
        if not self._subscribed or self._reader is None:
            raise RuntimeError("Must call connect() before reading events")

        try:
            while self._pending_events and not self._closed:
                yield self._pending_events.popleft()

            while not self._closed:
                if self._reader is None:
                    break
                chunk = await self._reader.read(4096)
                if not chunk:
                    # Connection closed by server
                    break
                messages = self._buffer.feed(chunk)
                for msg in messages:
                    yield msg
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            await self.close()

    async def close(self) -> None:
        """Close the socket connection cleanly."""
        self._closed = True
        self._subscribed = False
        self._pending_events.clear()
        self._buffer.clear()
        if self._writer is not None:
            writer = self._writer
            self._writer = None
            self._reader = None
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
