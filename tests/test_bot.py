import datetime
import tomllib
from pathlib import Path
from types import SimpleNamespace

import hikari
import pytest
import yaml

from dragonpaw_bot.bot import (
    STATE_DIR,
    _state_to_yaml_dict,
    _yaml_dict_to_state,
    config_parse_toml,
    state_load_yaml,
    state_path,
    state_save_pickle,
    state_save_yaml,
)
from dragonpaw_bot.structs import GuildState, RoleMenuOptionState

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _sample_state() -> GuildState:
    return GuildState(
        id=hikari.Snowflake(99),
        name="Test Guild",
        config_url="https://example.com/config.toml",
        config_last=datetime.datetime(2025, 6, 1, 0, 0, 0),
        role_emojis={
            (hikari.Snowflake(10), "⭐"): RoleMenuOptionState(
                add_role_id=hikari.Snowflake(20),
                remove_role_ids=[],
            ),
            (hikari.Snowflake(10), "🔥"): RoleMenuOptionState(
                add_role_id=hikari.Snowflake(30),
                remove_role_ids=[hikari.Snowflake(20)],
            ),
        },
        role_names={hikari.Snowflake(20): "Star", hikari.Snowflake(30): "Fire"},
    )


def test_state_path_default_extension():
    p = state_path(hikari.Snowflake(42))
    assert p.name == "42.toml"
    assert p.parent == STATE_DIR


def test_state_path_custom_extension():
    p = state_path(hikari.Snowflake(42), extention="yaml")
    assert p.name == "42.yaml"


def test_config_parse_toml_valid():
    toml_text = """
log_channel = "bot-logs"

[lobby]
channel = "welcome"
kick_after_days = 3
role = "New Member"
rules = "Be nice"
welcome_message = "Hello!"

[roles]
channel = "role-select"

[[roles.menu]]
name = "Colors"
description = "Pick your color"

[[roles.menu.options]]
role = "Red"
emoji = "red_circle"
description = "Red role"
"""
    guild = SimpleNamespace(name="TestGuild", id=hikari.Snowflake(1))
    config = config_parse_toml(guild=guild, text=toml_text)
    assert config.log_channel == "bot-logs"
    assert config.lobby is not None
    assert config.lobby.channel == "welcome"
    assert config.lobby.kick_after_days == 3
    assert config.roles is not None
    assert config.roles.channel == "role-select"


def test_config_parse_toml_bad_toml():
    guild = SimpleNamespace(name="TestGuild", id=hikari.Snowflake(1))
    with pytest.raises(tomllib.TOMLDecodeError):
        config_parse_toml(guild=guild, text="[invalid toml ===")


def test_state_to_yaml_dict_nested_structure():
    state = _sample_state()
    data = _state_to_yaml_dict(state)

    # role_emojis should be nested: {msg_id: {emoji: opt_state}}
    assert 10 in data["role_emojis"]
    assert "⭐" in data["role_emojis"][10]
    assert "🔥" in data["role_emojis"][10]
    assert data["role_emojis"][10]["⭐"]["add_role_id"] == 20
    assert data["role_emojis"][10]["🔥"]["add_role_id"] == 30
    assert data["role_emojis"][10]["🔥"]["remove_role_ids"] == [20]

    # role_names keys should be int
    assert 20 in data["role_names"]
    assert 30 in data["role_names"]


def test_yaml_dict_round_trip():
    state = _sample_state()
    data = _state_to_yaml_dict(state)
    restored = _yaml_dict_to_state(data)

    assert restored.id == state.id
    assert restored.name == state.name
    assert restored.role_emojis == state.role_emojis
    assert restored.role_names == state.role_names


def test_yaml_round_trip(state_dir):
    state = _sample_state()

    state_save_yaml(state)

    expected_file = state_dir / "99.yaml"
    assert expected_file.exists()

    # Verify the YAML is human-readable
    with open(expected_file) as f:
        raw = yaml.safe_load(f)
    assert raw["name"] == "Test Guild"
    assert 10 in raw["role_emojis"]

    loaded = state_load_yaml(hikari.Snowflake(99))
    assert loaded is not None
    assert loaded.id == state.id
    assert loaded.name == state.name
    assert loaded.role_emojis == state.role_emojis
    assert loaded.role_names == state.role_names


def test_yaml_load_missing_file(state_dir):
    result = state_load_yaml(hikari.Snowflake(999))
    assert result is None


def test_auto_migration_from_pickle(state_dir):
    state = _sample_state()

    # Save as pickle (old format)
    state_save_pickle(state)
    pickle_file = state_dir / "99.pickle"
    assert pickle_file.exists()

    # Load via YAML loader — should auto-migrate
    loaded = state_load_yaml(hikari.Snowflake(99))
    assert loaded is not None
    assert loaded.id == state.id
    assert loaded.name == state.name
    assert loaded.role_emojis == state.role_emojis
    assert loaded.role_names == state.role_names

    # Pickle should be deleted, YAML should exist
    assert not pickle_file.exists()
    yaml_file = state_dir / "99.yaml"
    assert yaml_file.exists()


def test_config_parse_dragonpaw_gist():
    """Parse the real-world config from the dragonpaw gist."""
    toml_text = (FIXTURES_DIR / "dragonpaw_gist.toml").read_text()
    guild = SimpleNamespace(name="TestGuild", id=hikari.Snowflake(1))
    config = config_parse_toml(guild=guild, text=toml_text)

    # Lobby
    assert config.lobby is not None
    assert config.lobby.channel == "unverified"
    assert config.lobby.kick_after_days == 10
    assert config.lobby.role == "Unverified"
    assert "{name}" in config.lobby.welcome_message

    # Roles
    assert config.roles is not None
    assert config.roles.channel == "roles"
    assert len(config.roles.menu) == 6

    # Check each menu by name
    menus = {m.name: m for m in config.roles.menu}

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
    assert len(pings.options) == 8
