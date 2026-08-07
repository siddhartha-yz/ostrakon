from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from ostrakon.onebot import OneBotWebSocketClient


@pytest.mark.asyncio
async def test_event_handler_can_call_api_without_deadlocking() -> None:
    handler_completed = asyncio.Event()
    api_request_seen = asyncio.Event()

    async def server_handler(ws) -> None:
        await ws.send(
            json.dumps(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "group_id": 10001,
                    "user_id": 20001,
                    "raw_message": "/ostrakon status",
                    "self_id": 99999,
                }
            )
        )
        request = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        api_request_seen.set()
        await ws.send(
            json.dumps(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"role": "admin"},
                    "echo": request["echo"],
                }
            )
        )
        await asyncio.wait_for(handler_completed.wait(), timeout=2)

    async with websockets.serve(server_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = OneBotWebSocketClient(f"ws://127.0.0.1:{port}", request_timeout=1)

        async def handler(_event) -> None:
            result = await client.call(
                "get_group_member_info",
                {"group_id": "10001", "user_id": "20001", "no_cache": True},
            )
            assert result == {"role": "admin"}
            handler_completed.set()

        runner = asyncio.create_task(client.run_forever(handler))
        try:
            await asyncio.wait_for(api_request_seen.wait(), timeout=2)
            await asyncio.wait_for(handler_completed.wait(), timeout=2)
        finally:
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner
