from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .ostrakon.config import Settings
from .ostrakon.db import Store
from .ostrakon.onebot import AstrBotOneBotGateway
from .ostrakon.service import OstrakonService


class ReactionNoticeFilter(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        raw = getattr(event.message_obj, "raw_message", None)
        return bool(
            isinstance(raw, Mapping)
            and raw.get("post_type") == "notice"
            and raw.get("notice_type") == "group_msg_emoji_like"
        )


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        data_dir = Path(get_astrbot_plugin_data_path()) / "ostrakon"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings.from_mapping(
            config,
            database_path=data_dir / "ostrakon.sqlite3",
        )
        self.settings.validate()
        self.store = Store(self.settings.database_path)
        self.service = OstrakonService(self.settings, self.store)
        if self.settings.target_reaction_id:
            logger.info(
                "Ostrakon loaded: groups=%d reaction=%s threshold=%d",
                len(self.settings.enabled_groups),
                self.settings.target_reaction_id,
                self.settings.vote_threshold,
            )
        else:
            logger.warning("Ostrakon loaded in diagnostic mode: target_reaction_id is empty")

    @filter.command_group("ostrakon")
    def ostrakon(self) -> None:
        pass

    @ostrakon.command("status")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def status(self, event: AstrMessageEvent) -> None:
        await self._dispatch(event)
        event.stop_event()

    @ostrakon.command("reset")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def reset(self, event: AstrMessageEvent) -> None:
        await self._dispatch(event)
        event.stop_event()

    @filter.custom_filter(ReactionNoticeFilter, priority=10)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def on_reaction_notice(self, event: AstrMessageEvent) -> None:
        await self._dispatch(event)
        event.stop_event()

    async def _dispatch(self, event: AstrMessageEvent) -> None:
        raw = getattr(event.message_obj, "raw_message", None)
        bot = getattr(event, "bot", None)
        if not isinstance(raw, Mapping) or bot is None:
            logger.warning("Ostrakon ignored an aiocqhttp event without raw OneBot data/bot")
            return
        payload: dict[str, Any] = dict(raw)
        gateway = AstrBotOneBotGateway(
            bot,
            self_id=str(event.message_obj.self_id or payload.get("self_id") or "") or None,
        )
        await self.service.handle_event(payload, gateway)

    async def terminate(self) -> None:
        await self.store.close()
