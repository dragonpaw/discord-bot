import datetime
import logging
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import hikari
import pydantic
import pytest
import yaml

import dragonpaw_bot.bot as bot_module
from dragonpaw_bot.bot import (
    STATE_DIR,
    _state_to_yaml_dict,
    _yaml_dict_to_state,
    client,
    loader,
    on_component_interaction,
    state_load_yaml,
    state_path,
    state_save_yaml,
)
from dragonpaw_bot.plugins.role_menus.commands import parse_role_config
from dragonpaw_bot.structs import GuildState

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _sample_state() -> GuildState:
    return GuildState(
        id=hikari.Snowflake(99),
        name="Test Guild",
        config_url="https://example.com/config.toml",
        config_last=datetime.datetime(2025, 6, 1, 0, 0, 0),
    )


def test_state_path_default_extension():
    p = state_path(hikari.Snowflake(42))
    assert p.name == "42.toml"
    assert p.parent == STATE_DIR


def test_state_path_custom_extension():
    p = state_path(hikari.Snowflake(42), extension="yaml")
    assert p.name == "42.yaml"


def test_parse_role_config_valid():
    toml_text = """
channel = "role-select"

[[menu]]
name = "Colors"
description = "Pick your color"
options = [
  { role = "Red", emoji = "red_circle", description = "Red role" },
]
"""
    config = parse_role_config(toml_text)
    assert config.channel == "role-select"
    assert len(config.menu) == 1
    assert config.menu[0].name == "Colors"
    assert config.menu[0].options[0].role == "Red"


def test_parse_role_config_bad_toml():
    with pytest.raises(tomllib.TOMLDecodeError):
        parse_role_config("[invalid toml ===")


def test_parse_role_config_missing_channel():
    toml_text = """
[[menu]]
name = "Test"
options = [{ role = "X", description = "Y" }]
"""
    with pytest.raises(pydantic.ValidationError):
        parse_role_config(toml_text)


def test_parse_role_config_missing_menu():
    with pytest.raises(pydantic.ValidationError):
        parse_role_config('channel = "roles"\n')


def test_parse_role_config_rejects_old_nested_format():
    """The old [roles]-nested format should not parse as the new flat format."""
    old_format = """
[roles]
channel = "roles"

[[roles.menu]]
name = "Test"

[[roles.menu.options]]
role = "X"
description = "Y"
"""
    with pytest.raises(pydantic.ValidationError):
        parse_role_config(old_format)


def test_state_to_yaml_dict():
    state = _sample_state()
    data = _state_to_yaml_dict(state)
    assert data["name"] == "Test Guild"
    assert data["config_url"] == "https://example.com/config.toml"


def test_yaml_dict_round_trip():
    state = _sample_state()
    data = _state_to_yaml_dict(state)
    restored = _yaml_dict_to_state(data)

    assert restored.id == state.id
    assert restored.name == state.name


def test_yaml_round_trip(state_dir):
    state = _sample_state()

    state_save_yaml(state)

    expected_file = state_dir / "99.yaml"
    assert expected_file.exists()

    # Verify the YAML is human-readable
    with open(expected_file) as f:
        raw = yaml.safe_load(f)
    assert raw["name"] == "Test Guild"

    loaded = state_load_yaml(hikari.Snowflake(99))
    assert loaded is not None
    assert loaded.id == state.id
    assert loaded.name == state.name


def test_yaml_load_missing_file(state_dir):
    result = state_load_yaml(hikari.Snowflake(999))
    assert result is None


def test_yaml_load_strips_legacy_role_fields(state_dir):
    """Old YAML files with role_emojis/role_names/role_channel_id should load fine."""
    legacy_data = {
        "id": 99,
        "name": "Legacy Guild",
        "config_url": "https://example.com/config.toml",
        "config_last": "2025-06-01T00:00:00",
        "role_emojis": {10: {"⭐": {"add_role_id": 20, "remove_role_ids": []}}},
        "role_names": {20: "Star"},
        "role_channel_id": 333,
    }
    yaml_file = state_dir / "99.yaml"
    with open(yaml_file, "w") as f:
        yaml.dump(legacy_data, f)

    loaded = state_load_yaml(hikari.Snowflake(99))
    assert loaded is not None
    assert loaded.id == hikari.Snowflake(99)
    assert loaded.name == "Legacy Guild"
    # Legacy fields should not be present
    assert not hasattr(loaded, "role_emojis")
    assert not hasattr(loaded, "role_names")
    assert not hasattr(loaded, "role_channel_id")


def test_parse_role_config_dragonpaw_gist():
    """Parse the real-world config from the dragonpaw gist."""
    toml_text = (FIXTURES_DIR / "dragonpaw_gist.toml").read_text()
    config = parse_role_config(toml_text)

    assert config.channel == "roles"
    assert len(config.menu) == 6

    # Check each menu by name
    menus = {m.name: m for m in config.menu}

    ds = menus["D/s roles"]
    assert ds.single is True
    assert len(ds.options) == 3
    assert {o.role for o in ds.options} == {"Dominant", "submissive", "Switch"}

    gender = menus["Gender"]
    assert gender.single is True
    assert len(gender.options) == 4

    trans = menus["Trans"]
    assert trans.single is False
    assert len(trans.options) == 1

    dm = menus["DM Permission"]
    assert dm.single is True
    assert len(dm.options) == 2

    kinks = menus["Misc Kinks"]
    assert kinks.single is False
    assert len(kinks.options) == 12

    pings = menus["Pings"]
    assert pings.single is False
    assert len(pings.options) == 9


async def test_bot_startup_loads_extensions():
    """Verify the bot loader and all extensions load without errors.

    This exercises the same code path as on_starting: loading the
    bot.py loader into the client, then loading all plugin extensions.
    """
    await loader.add_to_client(client)
    await client.load_extensions(
        "dragonpaw_bot.plugins.lobby",
        "dragonpaw_bot.plugins.role_menus",
        "dragonpaw_bot.plugins.subday",
    )


def _make_component_event(custom_id: str) -> hikari.InteractionCreateEvent:
    """Build a fake InteractionCreateEvent with a ComponentInteraction."""
    user = Mock(spec=hikari.User)
    user.username = "testuser"
    interaction = Mock(spec=hikari.ComponentInteraction)
    interaction.custom_id = custom_id
    interaction.user = user
    interaction.guild_id = hikari.Snowflake(1)
    interaction.create_initial_response = AsyncMock()
    event = Mock(spec=hikari.InteractionCreateEvent)
    event.interaction = interaction
    return event


async def test_unknown_interaction_id_logs_error(caplog):
    """Unknown custom_id values should emit an error log."""
    event = _make_component_event("totally_bogus_id")
    with caplog.at_level(logging.ERROR, logger="dragonpaw_bot.bot"):
        await on_component_interaction(event)
    assert any("Unhandled interaction" in r.message for r in caplog.records)
    assert any("totally_bogus_id" in r.message for r in caplog.records)


async def test_handler_exception_logs_and_responds(monkeypatch, caplog):
    """When a matched handler raises, the dispatcher should log and send an error response."""

    async def _exploding_handler(interaction):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        bot_module,
        "_INTERACTION_ROUTES",
        [("test_prefix:", _exploding_handler, "testing")],
    )

    event = _make_component_event("test_prefix:123")
    interaction = event.interaction

    with caplog.at_level(logging.ERROR, logger="dragonpaw_bot.bot"):
        await on_component_interaction(event)

    assert any("Error handling interaction" in r.message for r in caplog.records)
    interaction.create_initial_response.assert_called_once()
    call_kwargs = interaction.create_initial_response.call_args
    assert "error occurred" in str(call_kwargs).lower()
