from __future__ import annotations

from typing import Any

import pytest

from ostrakon.onebot import AstrBotOneBotGateway, OneBotAPIError


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = False

    async def call_action(self, action: str, **params: Any) -> Any:
        self.calls.append((action, params))
        if self.fail:
            raise RuntimeError("boom")
        return {"ok": True}


@pytest.mark.asyncio
async def test_gateway_routes_onebot_action_with_numeric_ids() -> None:
    bot = FakeBot()
    gateway = AstrBotOneBotGateway(bot, self_id="99999")

    result = await gateway.call(
        "set_group_ban",
        {"group_id": "10001", "user_id": "20001", "duration": 600},
    )

    assert result == {"ok": True}
    assert bot.calls == [
        (
            "set_group_ban",
            {"group_id": 10001, "user_id": 20001, "duration": 600, "self_id": 99999},
        )
    ]


@pytest.mark.asyncio
async def test_gateway_wraps_aiocqhttp_action_errors() -> None:
    bot = FakeBot()
    bot.fail = True
    gateway = AstrBotOneBotGateway(bot)

    with pytest.raises(OneBotAPIError, match="get_msg failed"):
        await gateway.call("get_msg", {"message_id": "30001"})
