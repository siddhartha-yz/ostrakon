from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv_set(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be > 0")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    onebot_ws_url: str
    onebot_access_token: str
    enabled_groups: frozenset[str]
    target_reaction_id: str
    vote_threshold: int
    first_mute_seconds: int
    repeat_mute_seconds: int
    repeat_window_seconds: int
    database_path: Path
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            onebot_ws_url=os.getenv("ONEBOT_WS_URL", "ws://napcat:3001").strip(),
            onebot_access_token=os.getenv("ONEBOT_ACCESS_TOKEN", "").strip(),
            enabled_groups=_csv_set(os.getenv("ENABLED_GROUPS", "")),
            target_reaction_id=os.getenv("TARGET_REACTION_ID", "").strip(),
            vote_threshold=_positive_int("VOTE_THRESHOLD", 5),
            first_mute_seconds=_positive_int("FIRST_MUTE_SECONDS", 600),
            repeat_mute_seconds=_positive_int("REPEAT_MUTE_SECONDS", 7200),
            repeat_window_seconds=_positive_int("REPEAT_WINDOW_SECONDS", 7 * 24 * 3600),
            database_path=Path(os.getenv("DATABASE_PATH", "/data/ostrakon.sqlite3")),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )

    def validate(self) -> None:
        if not self.onebot_ws_url.startswith(("ws://", "wss://")):
            raise ValueError("ONEBOT_WS_URL must start with ws:// or wss://")
        if self.target_reaction_id and not self.enabled_groups:
            raise ValueError(
                "ENABLED_GROUPS must contain at least one group ID when moderation is enabled"
            )
