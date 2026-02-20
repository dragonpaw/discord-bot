import datetime

import hikari
import pydantic
import pytest

from dragonpaw_bot.structs import (
    GuildConfig,
    GuildState,
    LobbyConfig,
    RoleMenuOptionState,
    RolesConfig,
)


def test_guild_config_minimal():
    """Nullable fields can be explicitly set to None."""
    config = GuildConfig.model_validate(
        {"lobby": None, "roles": None, "log_channel": None}
    )
    assert config.lobby is None
    assert config.roles is None
    assert config.log_channel is None


def test_guild_config_full():
    data = {
        "log_channel": "bot-logs",
        "lobby": {
            "channel": "welcome",
            "kick_after_days": 7,
            "role": "New Member",
            "rules": "Be nice.",
            "welcome_message": "Hello {user}!",
            "click_for_rules": True,
        },
        "roles": {
            "channel": "role-select",
            "menu": [
                {
                    "name": "Colors",
                    "description": "Pick a color",
                    "options": [
                        {"role": "Red", "emoji": "🔴", "description": "Red role"},
                    ],
                }
            ],
        },
    }
    config = GuildConfig.model_validate(data)
    assert config.log_channel == "bot-logs"

    assert isinstance(config.lobby, LobbyConfig)
    assert config.lobby.channel == "welcome"
    assert config.lobby.kick_after_days == 7
    assert config.lobby.click_for_rules is True

    assert isinstance(config.roles, RolesConfig)
    assert config.roles.channel == "role-select"
    assert len(config.roles.menu) == 1
    assert config.roles.menu[0].name == "Colors"
    assert len(config.roles.menu[0].options) == 1


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
        role_channel_id=hikari.Snowflake(333),
        role_emojis={
            (hikari.Snowflake(444), "🔴"): RoleMenuOptionState(
                add_role_id=hikari.Snowflake(555),
                remove_role_ids=[hikari.Snowflake(666)],
            ),
        },
        role_names={hikari.Snowflake(555): "Red"},
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
    assert restored.role_emojis == state.role_emojis
    assert restored.role_names == state.role_names
