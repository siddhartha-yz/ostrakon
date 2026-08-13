from pathlib import Path

import pytest

from ostrakon.config import Settings


def test_plugin_config_defaults_and_group_ids() -> None:
    settings = Settings.from_mapping(
        {"enabled_groups": [10001, "10002"], "target_reaction_id": "326"},
        database_path=Path("state.sqlite3"),
    )

    assert settings.enabled_groups == frozenset({"10001", "10002"})
    assert settings.target_reaction_id == "326"
    assert settings.vote_threshold == 10
    assert settings.first_mute_seconds == 600
    assert settings.repeat_mute_seconds == 7200
    assert settings.repeat_window_seconds == 7 * 24 * 3600


def test_active_policy_requires_group_whitelist() -> None:
    settings = Settings.from_mapping(
        {"enabled_groups": [], "target_reaction_id": "326"},
        database_path=Path("state.sqlite3"),
    )

    with pytest.raises(ValueError, match="enabled_groups"):
        settings.validate()
