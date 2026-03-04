import datetime

import hikari
import pydantic
import pytest

from dragonpaw_bot.structs import (
    GuildConfig,
    GuildState,
    LobbyConfig,
)


def test_guild_config_minimal():
    """Nullable fields can be explicitly set to None."""
    config = GuildConfig.model_validate({"lobby": None})
    assert config.lobby is None


def test_guild_config_full():
    data = {
        "lobby": {
            "channel": "welcome",
            "kick_after_days": 7,
            "role": "New Member",
            "rules": "Be nice.",
            "welcome_message": "Hello {user}!",
            "click_for_rules": True,
        },
    }
    config = GuildConfig.model_validate(data)

    assert isinstance(config.lobby, LobbyConfig)
    assert config.lobby.channel == "welcome"
    assert config.lobby.kick_after_days == 7
    assert config.lobby.click_for_rules is True


def test_guild_config_invalid_field():
    with pytest.raises(pydantic.ValidationError):
        GuildConfig.model_validate(
            {"lobby": {"channel": "x", "kick_after_days": "not_a_number"}}
        )


def test_guild_state_round_trip():
    """model_dump() → model_validate() should produce an equal object."""
    state = GuildState(
        id=hikari.Snowflake(123456789),
        name="Test Guild",
        config_url="https://example.com/config.toml",
        config_last=datetime.datetime(2025, 1, 1, 12, 0, 0),
        lobby_role_id=hikari.Snowflake(111),
        lobby_welcome_message="Welcome!",
        lobby_channel_id=hikari.Snowflake(222),
        lobby_click_for_rules=True,
        lobby_kick_days=7,
        lobby_rules="Be nice.",
        log_channel_id=hikari.Snowflake(777),
    )

    dumped = state.model_dump()
    restored = GuildState.model_validate(dumped)

    assert restored.id == state.id
    assert restored.name == state.name
    assert restored.config_url == state.config_url
    assert restored.config_last == state.config_last
    assert restored.lobby_role_id == state.lobby_role_id
    assert restored.lobby_click_for_rules is True
