from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _id_set(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        values: Sequence[Any] = value.split(",")
    elif isinstance(value, Sequence):
        values = value
    else:
        values = ()
    return frozenset(text for item in values if (text := str(item).strip()))


def _positive_int(config: Mapping[str, Any], name: str, default: int) -> int:
    raw = config.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be > 0")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    enabled_groups: frozenset[str]
    target_reaction_id: str
    vote_threshold: int
    first_mute_seconds: int
    repeat_mute_seconds: int
    repeat_window_seconds: int
    database_path: Path

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any], *, database_path: Path) -> Settings:
        return cls(
            enabled_groups=_id_set(config.get("enabled_groups", [])),
            target_reaction_id=str(config.get("target_reaction_id", "")).strip(),
            vote_threshold=_positive_int(config, "vote_threshold", 10),
            first_mute_seconds=_positive_int(config, "first_mute_seconds", 600),
            repeat_mute_seconds=_positive_int(config, "repeat_mute_seconds", 7200),
            repeat_window_seconds=_positive_int(config, "repeat_window_seconds", 7 * 24 * 3600),
            database_path=database_path,
        )

    def validate(self) -> None:
        if self.target_reaction_id and not self.enabled_groups:
            raise ValueError(
                "enabled_groups must contain at least one group ID when moderation is enabled"
            )
