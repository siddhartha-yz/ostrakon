from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)


class OneBotAPIError(RuntimeError):
    pass


class OneBotGateway(Protocol):
    self_id: str | None

    async def call(self, action: str, params: dict[str, Any]) -> Any: ...


EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class OneBotWebSocketClient:
    def __init__(self, url: str, access_token: str = "", request_timeout: float = 10.0) -> None:
        self.url = url
        self.access_token = access_token
        self.request_timeout = request_timeout
        self.self_id: str | None = None
        self._ws: ClientConnection | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._send_lock = asyncio.Lock()
        self._connected = asyncio.Event()

    async def run_forever(self, handler: EventHandler) -> None:
        delay = 1.0
        while True:
            try:
                headers = None
                if self.access_token:
                    headers = {"Authorization": f"Bearer {self.access_token}"}
                async with websockets.connect(
                    self.url,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2**22,
                ) as ws:
                    self._ws = ws
                    self._connected.set()
                    logger.info("connected to OneBot WebSocket")
                    delay = 1.0
                    await self._receive_loop(ws, handler)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("OneBot WebSocket disconnected: %s", exc)
            finally:
                self._connected.clear()
                self._ws = None
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(OneBotAPIError("OneBot connection lost"))
                self._pending.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _receive_loop(self, ws: ClientConnection, handler: EventHandler) -> None:
        async for raw in ws:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("discarding invalid JSON from OneBot")
                continue
            if not isinstance(payload, dict):
                continue
            if "echo" in payload and payload.get("echo") is not None:
                echo = str(payload["echo"])
                future = self._pending.pop(echo, None)
                if future is not None and not future.done():
                    future.set_result(payload)
                continue
            if payload.get("self_id") is not None:
                self.self_id = str(payload["self_id"])
            try:
                await handler(payload)
            except Exception:
                logger.exception("unhandled error while processing OneBot event")

    async def call(self, action: str, params: dict[str, Any]) -> Any:
        await asyncio.wait_for(self._connected.wait(), timeout=self.request_timeout)
        ws = self._ws
        if ws is None:
            raise OneBotAPIError("OneBot WebSocket is not connected")
        echo = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[echo] = future
        request = {"action": action, "params": params, "echo": echo}
        try:
            async with self._send_lock:
                await ws.send(json.dumps(request, ensure_ascii=False))
            response = await asyncio.wait_for(future, timeout=self.request_timeout)
        except Exception:
            self._pending.pop(echo, None)
            raise
        if response.get("status") != "ok" or int(response.get("retcode", -1)) != 0:
            message = response.get("message") or response.get("wording") or "unknown OneBot error"
            raise OneBotAPIError(f"{action} failed: retcode={response.get('retcode')} {message}")
        return response.get("data")
