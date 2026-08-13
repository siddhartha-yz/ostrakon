from __future__ import annotations

from typing import Any, Protocol


class OneBotAPIError(RuntimeError):
    pass


class OneBotGateway(Protocol):
    self_id: str | None

    async def call(self, action: str, params: dict[str, Any]) -> Any: ...


class AstrBotOneBotGateway:
    """Adapt AstrBot's aiocqhttp event bot to Ostrakon's OneBot API interface."""

    def __init__(self, bot: Any, *, self_id: str | None = None) -> None:
        self.bot = bot
        self.self_id = str(self_id).strip() if self_id else None

    async def call(self, action: str, params: dict[str, Any]) -> Any:
        routed = dict(params)
        if self.self_id and "self_id" not in routed:
            routed["self_id"] = self._numeric_id(self.self_id)
        for key in ("group_id", "user_id", "message_id"):
            if key in routed:
                routed[key] = self._numeric_id(routed[key])
        try:
            return await self.bot.call_action(action, **routed)
        except Exception as exc:
            raise OneBotAPIError(f"{action} failed: {exc}") from exc

    @staticmethod
    def _numeric_id(value: Any) -> Any:
        text = str(value).strip()
        return int(text) if text.isdigit() else value
