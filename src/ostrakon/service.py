from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

from ostrakon.config import Settings
from ostrakon.db import PunishmentClaim, Store
from ostrakon.onebot import OneBotAPIError, OneBotGateway

logger = logging.getLogger(__name__)


class OstrakonService:
    def __init__(self, settings: Settings, store: Store, gateway: OneBotGateway) -> None:
        self.settings = settings
        self.store = store
        self.gateway = gateway
        self._target_locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def handle_event(self, event: dict[str, Any]) -> None:
        if event.get("post_type") != "notice" or event.get("notice_type") != "group_msg_emoji_like":
            return

        group_id = self._id(event.get("group_id"))
        message_id = self._id(event.get("message_id"))
        if not group_id or not message_id:
            return
        if self.settings.target_reaction_id and group_id not in self.settings.enabled_groups:
            return
        if (
            not self.settings.target_reaction_id
            and self.settings.enabled_groups
            and group_id not in self.settings.enabled_groups
        ):
            return

        likes = event.get("likes")
        if not isinstance(likes, list):
            logger.warning(
                "reaction event missing likes: group_id=%s message_id=%s",
                group_id,
                message_id,
            )
            return

        for like in likes:
            if not isinstance(like, dict):
                continue
            emoji_id = self._id(like.get("emoji_id"))
            if not emoji_id:
                continue
            if not self.settings.target_reaction_id:
                emoji_type = await self._diagnostic_emoji_type(message_id, emoji_id)
                logger.info(
                    "reaction diagnostic: emoji_id=%s emoji_type=%s "
                    "message_id=%s group_id=%s is_add=%s",
                    emoji_id,
                    emoji_type,
                    message_id,
                    group_id,
                    event.get("is_add"),
                )
                continue
            if emoji_id != self.settings.target_reaction_id:
                continue
            await self._handle_target_reaction(event, group_id, message_id, emoji_id)

    async def _handle_target_reaction(
        self,
        event: dict[str, Any],
        group_id: str,
        message_id: str,
        emoji_id: str,
    ) -> None:
        state = await self.store.get_message(group_id, message_id)
        if state is None:
            sender_id = await self._resolve_message_sender(group_id, message_id)
            if sender_id is None:
                return
            state = await self.store.ensure_message(group_id, message_id, sender_id)

        voter_id = self._id(event.get("user_id") or event.get("operator_id"))
        is_add = event.get("is_add")
        if voter_id and voter_id != "0" and isinstance(is_add, bool):
            count = await self.store.apply_vote(
                group_id, message_id, voter_id, emoji_id, is_add
            )
        else:
            count = await self._reconcile_reaction_voters(group_id, message_id, emoji_id)
            if count is None:
                logger.warning(
                    "reaction event cannot be applied reliably: "
                    "group_id=%s message_id=%s emoji_id=%s",
                    group_id,
                    message_id,
                    emoji_id,
                )
                return

        logger.info(
            "reaction vote count changed: group_id=%s message_id=%s emoji_id=%s votes=%d",
            group_id,
            message_id,
            emoji_id,
            count,
        )
        claim = await self.store.claim_punishment(
            group_id=group_id,
            message_id=message_id,
            reaction_id=emoji_id,
            threshold=self.settings.vote_threshold,
            first_duration=self.settings.first_mute_seconds,
            repeat_duration=self.settings.repeat_mute_seconds,
            repeat_window_seconds=self.settings.repeat_window_seconds,
        )
        if claim is None:
            return

        logger.info(
            "reaction threshold reached: group_id=%s message_id=%s target_user_id=%s duration=%s",
            claim.group_id,
            claim.message_id,
            claim.target_user_id,
            claim.duration_seconds,
        )
        async with self._target_locks[(claim.group_id, claim.target_user_id)]:
            await self._execute_claim(claim)

    async def _resolve_message_sender(self, group_id: str, message_id: str) -> str | None:
        try:
            message = await self.gateway.call("get_msg", {"message_id": message_id})
        except Exception as exc:
            logger.warning(
                "failed to resolve reacted message: group_id=%s message_id=%s error=%s",
                group_id,
                message_id,
                exc,
            )
            return None
        if not isinstance(message, dict):
            logger.warning("get_msg returned invalid payload for message_id=%s", message_id)
            return None
        returned_group = self._id(message.get("group_id"))
        sender_id = self._id(message.get("user_id") or (message.get("sender") or {}).get("user_id"))
        if returned_group != group_id or not sender_id:
            logger.warning(
                "reacted message identity mismatch: event_group=%s resolved_group=%s message_id=%s",
                group_id,
                returned_group,
                message_id,
            )
            return None
        return sender_id

    async def _reconcile_reaction_voters(
        self, group_id: str, message_id: str, emoji_id: str
    ) -> int | None:
        try:
            data = await self.gateway.call(
                "get_emoji_likes",
                {
                    "group_id": group_id,
                    "message_id": message_id,
                    "emoji_id": emoji_id,
                    "count": 0,
                },
            )
        except Exception as exc:
            logger.warning(
                "cannot reconcile reaction voters: group_id=%s message_id=%s error=%s",
                group_id,
                message_id,
                exc,
            )
            return None
        if not isinstance(data, dict) or not isinstance(data.get("emoji_like_list"), list):
            return None
        voters = {
            voter_id
            for item in data["emoji_like_list"]
            if isinstance(item, dict) and (voter_id := self._id(item.get("user_id")))
        }
        return await self.store.reconcile_votes(group_id, message_id, emoji_id, voters)

    async def _diagnostic_emoji_type(self, message_id: str, emoji_id: str) -> str:
        try:
            message = await self.gateway.call("get_msg", {"message_id": message_id})
        except Exception:
            return "unknown"
        if not isinstance(message, dict):
            return "unknown"
        for item in message.get("emoji_likes_list") or []:
            if isinstance(item, dict) and self._id(item.get("emoji_id")) == emoji_id:
                return self._id(item.get("emoji_type")) or "unknown"
        return "unknown"

    async def _execute_claim(self, claim: PunishmentClaim) -> None:
        self_id = self.gateway.self_id
        if not self_id:
            try:
                login = await self.gateway.call("get_login_info", {})
                if isinstance(login, dict):
                    self_id = self._id(login.get("user_id"))
            except Exception as exc:
                await self.store.mark_failed(claim.group_id, claim.message_id, f"login info: {exc}")
                return
        if not self_id:
            await self.store.mark_failed(claim.group_id, claim.message_id, "missing bot self_id")
            return
        if claim.target_user_id == self_id:
            await self.store.mark_ineligible(
                claim.group_id, claim.message_id, "target is the bot itself"
            )
            logger.warning(
                "mute skipped because target is bot: group_id=%s message_id=%s",
                claim.group_id,
                claim.message_id,
            )
            return

        try:
            target = await self.gateway.call(
                "get_group_member_info",
                {"group_id": claim.group_id, "user_id": claim.target_user_id, "no_cache": True},
            )
            bot_member = await self.gateway.call(
                "get_group_member_info",
                {"group_id": claim.group_id, "user_id": self_id, "no_cache": True},
            )
        except Exception as exc:
            await self.store.mark_failed(
                claim.group_id, claim.message_id, f"permission precheck failed: {exc}"
            )
            logger.warning(
                "mute permission precheck failed: group_id=%s message_id=%s error=%s",
                claim.group_id,
                claim.message_id,
                exc,
            )
            return

        target_role = target.get("role") if isinstance(target, dict) else None
        bot_role = bot_member.get("role") if isinstance(bot_member, dict) else None
        if target_role in {"owner", "admin"}:
            await self.store.mark_ineligible(
                claim.group_id, claim.message_id, f"target role is {target_role}"
            )
            logger.warning(
                "mute skipped for privileged target: group_id=%s message_id=%s role=%s",
                claim.group_id,
                claim.message_id,
                target_role,
            )
            return
        if bot_role not in {"owner", "admin"}:
            await self.store.mark_failed(
                claim.group_id, claim.message_id, f"bot role is {bot_role or 'unknown'}"
            )
            logger.warning(
                "mute skipped because bot lacks permission: group_id=%s message_id=%s bot_role=%s",
                claim.group_id,
                claim.message_id,
                bot_role,
            )
            return

        attempts = await self.store.record_api_attempt(claim.group_id, claim.message_id)
        try:
            await self.gateway.call(
                "set_group_ban",
                {
                    "group_id": claim.group_id,
                    "user_id": claim.target_user_id,
                    "duration": claim.duration_seconds,
                },
            )
        except Exception as exc:
            confirmed = await self._confirm_target_is_muted(claim)
            if confirmed:
                await self.store.mark_success(
                    claim.group_id,
                    claim.message_id,
                    claim.target_user_id,
                    claim.duration_seconds,
                )
                logger.warning(
                    "mute response failed but mute state was confirmed: group_id=%s message_id=%s",
                    claim.group_id,
                    claim.message_id,
                )
                return
            exhausted = attempts >= 3
            await self.store.mark_failed(
                claim.group_id,
                claim.message_id,
                f"set_group_ban failed: {exc}",
                exhausted=exhausted,
            )
            logger.warning(
                "mute API failed: group_id=%s message_id=%s attempt=%d exhausted=%s error=%s",
                claim.group_id,
                claim.message_id,
                attempts,
                exhausted,
                exc,
            )
            return

        await self.store.mark_success(
            claim.group_id,
            claim.message_id,
            claim.target_user_id,
            claim.duration_seconds,
        )
        logger.info(
            "mute succeeded: group_id=%s message_id=%s target_user_id=%s duration=%d",
            claim.group_id,
            claim.message_id,
            claim.target_user_id,
            claim.duration_seconds,
        )

    async def _confirm_target_is_muted(self, claim: PunishmentClaim) -> bool:
        try:
            target = await self.gateway.call(
                "get_group_member_info",
                {"group_id": claim.group_id, "user_id": claim.target_user_id, "no_cache": True},
            )
        except (OneBotAPIError, TimeoutError):
            return False
        except Exception:
            return False
        if not isinstance(target, dict):
            return False
        raw = target.get("shut_up_timestamp")
        try:
            return float(raw or 0) > time.time() + 2
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _id(value: Any) -> str:
        if value is None or isinstance(value, bool):
            return ""
        text = str(value).strip()
        return text if text else ""
