import datetime

import hikari
import pytest

from dragonpaw_bot.structs import GuildState


def test_guild_state_minimal():
    """GuildState can be created with only required fields."""
    state = GuildState(
        id=hikari.Snowflake(123456789),
        name="Test Guild",
        config_url="https://example.com/config.toml",
        config_last=datetime.datetime(2025, 1, 1, 12, 0, 0),
    )
    assert state.id == hikari.Snowflake(123456789)
    assert state.log_channel_id is None


def test_guild_state_round_trip():
    """model_dump() → model_validate() should produce an equal object."""
    state = GuildState(
        id=hikari.Snowflake(123456789),
        name="Test Guild",
        config_url="https://example.com/config.toml",
        config_last=datetime.datetime(2025, 1, 1, 12, 0, 0),
        log_channel_id=hikari.Snowflake(777),
    )

    dumped = state.model_dump()
    restored = GuildState.model_validate(dumped)

    assert restored.id == state.id
    assert restored.name == state.name
    assert restored.config_url == state.config_url
    assert restored.config_last == state.config_last
    assert restored.log_channel_id == state.log_channel_id


def test_guild_state_missing_required_fields():
    """GuildState raises ValidationError when required fields are missing."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        GuildState.model_validate({"name": "Test Guild"})
