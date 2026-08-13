from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from ostrakon.config import Settings
from ostrakon.db import Store
from ostrakon.onebot import OneBotAPIError
from ostrakon.service import OstrakonService

TARGET = "233"
GROUP = "10001"
BOT = "99999"
AUTHOR = "20001"


class FakeGateway:
    def __init__(self) -> None:
        self.self_id: str | None = BOT
        self.messages: dict[str, dict[str, Any]] = {}
        self.members: dict[tuple[str, str], dict[str, Any]] = {}
        self.emoji_voters: dict[tuple[str, str, str], set[str]] = {}
        self.emoji_snapshot_available = False
        self.ban_calls: list[dict[str, Any]] = []
        self.sent_group_messages: list[dict[str, Any]] = []
        self.fail_bans = 0

    def add_message(self, group_id: str, message_id: str, author: str) -> None:
        self.messages[message_id] = {
            "message_id": message_id,
            "group_id": group_id,
            "user_id": author,
            "sender": {"user_id": author},
            "emoji_likes_list": [],
        }
        self.members.setdefault((group_id, author), {"role": "member", "shut_up_timestamp": 0})
        self.members.setdefault((group_id, BOT), {"role": "admin", "shut_up_timestamp": 0})

    async def call(self, action: str, params: dict[str, Any]) -> Any:
        if action == "get_msg":
            message = self.messages.get(str(params["message_id"]))
            if message is None:
                raise OneBotAPIError("message not found")
            return dict(message)
        if action == "get_login_info":
            return {"user_id": BOT}
        if action == "get_group_member_info":
            key = (str(params["group_id"]), str(params["user_id"]))
            return dict(self.members.get(key, {"role": "member", "shut_up_timestamp": 0}))
        if action == "get_emoji_likes":
            if not self.emoji_snapshot_available:
                return None
            key = (
                str(params["group_id"]),
                str(params["message_id"]),
                str(params["emoji_id"]),
            )
            return {
                "emoji_like_list": [
                    {"user_id": voter, "nick_name": ""}
                    for voter in sorted(self.emoji_voters.get(key, set()))
                ]
            }
        if action == "set_group_ban":
            call = {key: str(value) for key, value in params.items()}
            call["duration"] = int(params["duration"])
            self.ban_calls.append(call)
            if self.fail_bans > 0:
                self.fail_bans -= 1
                raise OneBotAPIError("simulated mute failure")
            key = (str(params["group_id"]), str(params["user_id"]))
            member = self.members.setdefault(key, {"role": "member"})
            member["shut_up_timestamp"] = time.time() + int(params["duration"])
            return None
        if action == "send_group_msg":
            self.sent_group_messages.append(dict(params))
            return {"message_id": "90001"}
        raise AssertionError(f"unexpected OneBot action: {action}")


def settings(path: Path, groups: frozenset[str] = frozenset({GROUP})) -> Settings:
    return Settings(
        enabled_groups=groups,
        target_reaction_id=TARGET,
        vote_threshold=5,
        first_mute_seconds=600,
        repeat_mute_seconds=7200,
        repeat_window_seconds=7 * 24 * 3600,
        database_path=path,
    )


def event(
    voter: str | None,
    *,
    group: str = GROUP,
    message: str = "30001",
    emoji: str = TARGET,
    is_add: bool | None = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "post_type": "notice",
        "notice_type": "group_msg_emoji_like",
        "group_id": group,
        "message_id": message,
        "likes": [{"emoji_id": emoji, "count": 1}],
        "self_id": BOT,
    }
    if voter is not None:
        payload["user_id"] = voter
    if is_add is not None:
        payload["is_add"] = is_add
    return payload


async def build(tmp_path: Path, *, groups: frozenset[str] = frozenset({GROUP})):
    gateway = FakeGateway()
    gateway.add_message(GROUP, "30001", AUTHOR)
    store = Store(tmp_path / "state.sqlite3")
    service = OstrakonService(settings(tmp_path / "state.sqlite3", groups), store, gateway)
    return service, store, gateway


def status_event(user_id: str, *, group: str = GROUP) -> dict[str, Any]:
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": group,
        "user_id": user_id,
        "raw_message": "/ostrakon status",
        "self_id": BOT,
    }


def reset_event(
    user_id: str,
    *,
    group: str = GROUP,
    reply_message_id: str | None = "30001",
) -> dict[str, Any]:
    message: list[dict[str, Any]] = []
    if reply_message_id is not None:
        message.append({"type": "reply", "data": {"id": reply_message_id}})
    message.append({"type": "text", "data": {"text": "/ostrakon reset"}})
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": group,
        "user_id": user_id,
        "raw_message": "/ostrakon reset",
        "message": message,
        "self_id": BOT,
    }


@pytest.mark.asyncio
async def test_admin_status_command_reports_health(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.members[(GROUP, "30000")] = {"role": "admin", "shut_up_timestamp": 0}
    try:
        await service.handle_event(status_event("30000"))
        assert len(gateway.sent_group_messages) == 1
        reply = gateway.sent_group_messages[0]
        assert str(reply["group_id"]) == GROUP
        assert "Ostrakon: active" in str(reply["message"])
        assert f"Reaction: {TARGET}" in str(reply["message"])
        assert "Threshold: 5" in str(reply["message"])
        assert "Mute: 10m / repeat 2h" in str(reply["message"])
        assert "Database: OK" in str(reply["message"])
        assert "OneBot: connected" in str(reply["message"])
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_regular_member_cannot_use_status_command(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.members[(GROUP, "30000")] = {"role": "member", "shut_up_timestamp": 0}
    try:
        await service.handle_event(status_event("30000"))
        assert gateway.sent_group_messages == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_status_command_ignored_outside_enabled_groups(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    other_group = "10002"
    gateway.members[(other_group, "30000")] = {"role": "owner", "shut_up_timestamp": 0}
    try:
        await service.handle_event(status_event("30000", group=other_group))
        assert gateway.sent_group_messages == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_admin_reset_clears_repeat_history_for_replied_member(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.members[(GROUP, "30000")] = {"role": "admin", "shut_up_timestamp": 0}
    gateway.add_message(GROUP, "30002", AUTHOR)
    await store.set_last_punished_at(GROUP, AUTHOR, time.time())
    try:
        await service.handle_event(reset_event("30000"))
        assert len(gateway.sent_group_messages) == 1
        assert "repeat punishment history reset" in str(
            gateway.sent_group_messages[0]["message"]
        )

        for voter in ("11", "12", "13", "14", "15"):
            await service.handle_event(event(voter, message="30002"))
        assert gateway.ban_calls[0]["duration"] == 600
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_regular_member_cannot_reset_repeat_history(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.members[(GROUP, "30000")] = {"role": "member", "shut_up_timestamp": 0}
    gateway.add_message(GROUP, "30002", AUTHOR)
    await store.set_last_punished_at(GROUP, AUTHOR, time.time())
    try:
        await service.handle_event(reset_event("30000"))
        assert gateway.sent_group_messages == []

        for voter in ("11", "12", "13", "14", "15"):
            await service.handle_event(event(voter, message="30002"))
        assert gateway.ban_calls[0]["duration"] == 7200
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_admin_reset_requires_reply(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.members[(GROUP, "30000")] = {"role": "owner", "shut_up_timestamp": 0}
    try:
        await service.handle_event(reset_event("30000", reply_message_id=None))
        assert len(gateway.sent_group_messages) == 1
        assert "reply to a member's message" in str(gateway.sent_group_messages[0]["message"])
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_four_votes_do_not_mute_fifth_mutes_once_and_sixth_does_not_repeat(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    try:
        for voter in ("1", "2", "3", "4"):
            await service.handle_event(event(voter))
        assert gateway.ban_calls == []

        await service.handle_event(event("5"))
        assert len(gateway.ban_calls) == 1
        assert gateway.ban_calls[0]["user_id"] == AUTHOR
        assert gateway.ban_calls[0]["duration"] == 600

        await service.handle_event(event("6"))
        assert len(gateway.ban_calls) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_duplicate_voter_counts_once(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    try:
        for _ in range(3):
            await service.handle_event(event("1"))
        for voter in ("2", "3", "4"):
            await service.handle_event(event(voter))
        assert gateway.ban_calls == []
        await service.handle_event(event("5"))
        assert len(gateway.ban_calls) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_non_target_emoji_is_ignored(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    try:
        for voter in ("1", "2", "3", "4", "5", "6"):
            await service.handle_event(event(voter, emoji="999"))
        assert gateway.ban_calls == []
        assert await store.get_message(GROUP, "30001") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reaction_removal_reduces_active_vote_count(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    try:
        for voter in ("1", "2", "3", "4"):
            await service.handle_event(event(voter))
        await service.handle_event(event("4", is_add=False))
        await service.handle_event(event("5"))
        assert gateway.ban_calls == []
        await service.handle_event(event("6"))
        assert len(gateway.ban_calls) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_repeat_within_seven_days_uses_two_hours(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.add_message(GROUP, "30002", AUTHOR)
    try:
        for voter in ("1", "2", "3", "4", "5"):
            await service.handle_event(event(voter, message="30001"))
        for voter in ("11", "12", "13", "14", "15"):
            await service.handle_event(event(voter, message="30002"))
        assert [call["duration"] for call in gateway.ban_calls] == [600, 7200]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_third_punishment_within_seven_days_stays_two_hours(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.add_message(GROUP, "30002", AUTHOR)
    gateway.add_message(GROUP, "30003", AUTHOR)
    try:
        for voter in ("1", "2", "3", "4", "5"):
            await service.handle_event(event(voter, message="30001"))
        for voter in ("11", "12", "13", "14", "15"):
            await service.handle_event(event(voter, message="30002"))
        for voter in ("21", "22", "23", "24", "25"):
            await service.handle_event(event(voter, message="30003"))
        assert [call["duration"] for call in gateway.ban_calls] == [600, 7200, 7200]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_repeat_after_seven_days_returns_to_ten_minutes(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    try:
        await store.set_last_punished_at(GROUP, AUTHOR, time.time() - 8 * 24 * 3600)
        for voter in ("1", "2", "3", "4", "5"):
            await service.handle_event(event(voter))
        assert gateway.ban_calls[0]["duration"] == 600
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_groups_and_same_message_id_are_isolated(tmp_path: Path):
    other_group = "10002"
    gateway = FakeGateway()
    store = Store(tmp_path / "state.sqlite3")
    service = OstrakonService(
        settings(tmp_path / "state.sqlite3", frozenset({GROUP, other_group})), store, gateway
    )
    await store.ensure_message(GROUP, "777", "20001")
    await store.ensure_message(other_group, "777", "20002")
    gateway.members[(GROUP, "20001")] = {"role": "member", "shut_up_timestamp": 0}
    gateway.members[(other_group, "20002")] = {"role": "member", "shut_up_timestamp": 0}
    gateway.members[(GROUP, BOT)] = {"role": "admin", "shut_up_timestamp": 0}
    gateway.members[(other_group, BOT)] = {"role": "admin", "shut_up_timestamp": 0}
    try:
        for voter in ("1", "2", "3", "4"):
            await service.handle_event(event(voter, group=GROUP, message="777"))
        for voter in ("11", "12", "13", "14", "15"):
            await service.handle_event(event(voter, group=other_group, message="777"))
        assert len(gateway.ban_calls) == 1
        assert gateway.ban_calls[0]["group_id"] == other_group
        assert gateway.ban_calls[0]["user_id"] == "20002"
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_role", ["admin", "owner"])
async def test_privileged_target_is_never_muted(tmp_path: Path, target_role: str):
    service, store, gateway = await build(tmp_path)
    gateway.members[(GROUP, AUTHOR)]["role"] = target_role
    try:
        for voter in ("1", "2", "3", "4", "5"):
            await service.handle_event(event(voter))
        assert gateway.ban_calls == []
        state = await store.get_message(GROUP, "30001")
        assert state is not None and state.status == "ineligible"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_bot_itself_is_never_muted(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.messages["30001"]["user_id"] = BOT
    gateway.messages["30001"]["sender"] = {"user_id": BOT}
    try:
        for voter in ("1", "2", "3", "4", "5"):
            await service.handle_event(event(voter))
        assert gateway.ban_calls == []
        state = await store.get_message(GROUP, "30001")
        assert state is not None and state.status == "ineligible"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_bot_without_admin_permission_fails_safely(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.members[(GROUP, BOT)]["role"] = "member"
    try:
        for voter in ("1", "2", "3", "4", "5"):
            await service.handle_event(event(voter))
        assert gateway.ban_calls == []
        state = await store.get_message(GROUP, "30001")
        assert state is not None and state.status == "mute_failed"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_onebot_mute_failure_remains_retryable_and_not_marked_punished(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.fail_bans = 1
    try:
        for voter in ("1", "2", "3", "4", "5"):
            await service.handle_event(event(voter))
        state = await store.get_message(GROUP, "30001")
        assert state is not None
        assert state.status == "mute_failed"
        assert state.api_attempts == 1
        assert state.punished_at is None
        assert len(gateway.ban_calls) == 1

        await service.handle_event(event("6"))
        state = await store.get_message(GROUP, "30001")
        assert state is not None and state.status == "punished"
        assert len(gateway.ban_calls) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_duplicate_reaction_event_cannot_repeat_successful_punishment(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    try:
        for voter in ("1", "2", "3", "4", "5"):
            await service.handle_event(event(voter))
        for _ in range(5):
            await service.handle_event(event("5"))
        assert len(gateway.ban_calls) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_fifth_and_sixth_votes_mute_at_most_once(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    try:
        for voter in ("1", "2", "3", "4"):
            await service.handle_event(event(voter))
        await asyncio.gather(service.handle_event(event("5")), service.handle_event(event("6")))
        assert len(gateway.ban_calls) == 1
        state = await store.get_message(GROUP, "30001")
        assert state is not None and state.status == "punished"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_successful_state_survives_restart_without_repunishing(tmp_path: Path):
    db_path = tmp_path / "state.sqlite3"
    gateway = FakeGateway()
    gateway.add_message(GROUP, "30001", AUTHOR)
    store = Store(db_path)
    service = OstrakonService(settings(db_path), store, gateway)
    for voter in ("1", "2", "3", "4", "5"):
        await service.handle_event(event(voter))
    assert len(gateway.ban_calls) == 1
    await store.close()

    reopened = Store(db_path)
    restarted = OstrakonService(settings(db_path), reopened, gateway)
    try:
        await restarted.handle_event(event("6"))
        assert len(gateway.ban_calls) == 1
        state = await reopened.get_message(GROUP, "30001")
        assert state is not None and state.status == "punished"
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_missing_operator_fields_fall_back_to_current_reaction_list(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.emoji_snapshot_available = True
    gateway.emoji_voters[(GROUP, "30001", TARGET)] = {"1", "2", "3", "4", "5"}
    try:
        await service.handle_event(event(None, is_add=None))
        assert len(gateway.ban_calls) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reaction_snapshot_recovers_votes_missed_while_offline(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    gateway.emoji_snapshot_available = True
    gateway.emoji_voters[(GROUP, "30001", TARGET)] = {"1", "2", "3", "4", "5", "6", "7"}
    try:
        await store.ensure_message(GROUP, "30001", AUTHOR)
        for voter in ("1", "2", "3", "4"):
            await store.apply_vote(GROUP, "30001", voter, TARGET, True)

        await service.handle_event(event("7"))

        assert len(gateway.ban_calls) == 1
        state = await store.get_message(GROUP, "30001")
        assert state is not None and state.status == "punished"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_disabled_group_is_ignored(tmp_path: Path):
    service, store, gateway = await build(tmp_path)
    try:
        for voter in ("1", "2", "3", "4", "5"):
            await service.handle_event(event(voter, group="55555"))
        assert gateway.ban_calls == []
    finally:
        await store.close()
