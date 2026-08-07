from __future__ import annotations

import asyncio
import logging

from ostrakon.config import Settings
from ostrakon.db import Store
from ostrakon.onebot import OneBotWebSocketClient
from ostrakon.service import OstrakonService


async def async_main() -> None:
    settings = Settings.from_env()
    settings.validate()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)
    if not settings.target_reaction_id:
        logger.warning(
            "TARGET_REACTION_ID is empty: diagnostic mode enabled; no moderation action will run"
        )

    store = Store(settings.database_path)
    gateway = OneBotWebSocketClient(
        settings.onebot_ws_url,
        access_token=settings.onebot_access_token,
    )
    service = OstrakonService(settings, store, gateway)
    try:
        await gateway.run_forever(service.handle_event)
    finally:
        await store.close()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
