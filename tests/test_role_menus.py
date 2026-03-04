# -*- coding: utf-8 -*-
"""Tests for the role_menus plugin package."""

import hikari
import pydantic
import pytest
import yaml

import dragonpaw_bot.plugins.role_menus.state as role_menus_state
from dragonpaw_bot.plugins.role_menus.commands import (
    _build_summary,
    _find_menu_state,
    build_menu_embed,
    build_menu_select,
)
from dragonpaw_bot.plugins.role_menus.models import (
    RoleMenuConfig,
    RoleMenuGuildState,
    RoleMenuOptionConfig,
    RoleMenuState,
    RolesConfig,
)

# ---------------------------------------------------------------------------- #
#                                    Models                                     #
# ---------------------------------------------------------------------------- #


def test_option_config_valid():
    opt = RoleMenuOptionConfig(role="Red", emoji="red_circle", description="Red role")
    assert opt.role == "Red"
    assert opt.emoji == "red_circle"
    assert opt.description == "Red role"


def test_option_config_emoji_optional():
    opt = RoleMenuOptionConfig(role="Red", description="Red role")
    assert opt.emoji is None


def test_option_config_empty_role_rejected():
    with pytest.raises(pydantic.ValidationError):
        RoleMenuOptionConfig(role="", description="Red role")


def test_option_config_empty_description_rejected():
    with pytest.raises(pydantic.ValidationError):
        RoleMenuOptionConfig(role="Red", description="")


def test_menu_config_25_options_ok():
    options = [
        RoleMenuOptionConfig(role=f"Role{i}", description=f"Desc {i}")
        for i in range(25)
    ]
    menu = RoleMenuConfig(name="Big Menu", options=options)
    assert len(menu.options) == 25


def test_menu_config_26_options_rejected():
    options = [
        RoleMenuOptionConfig(role=f"Role{i}", description=f"Desc {i}")
        for i in range(26)
    ]
    with pytest.raises(pydantic.ValidationError, match="25"):
        RoleMenuConfig(name="Too Big", options=options)


def test_menu_config_empty_options_rejected():
    with pytest.raises(pydantic.ValidationError):
        RoleMenuConfig(name="Empty", options=[])


def test_menu_config_empty_name_rejected():
    with pytest.raises(pydantic.ValidationError):
        RoleMenuConfig(
            name="",
            options=[RoleMenuOptionConfig(role="X", description="Y")],
        )


def test_roles_config_empty_channel_rejected():
    with pytest.raises(pydantic.ValidationError):
        RolesConfig(
            channel="",
            menu=[
                RoleMenuConfig(
                    name="M",
                    options=[RoleMenuOptionConfig(role="X", description="Y")],
                )
            ],
        )


def test_roles_config_empty_menu_rejected():
    with pytest.raises(pydantic.ValidationError):
        RolesConfig(channel="roles", menu=[])


def test_menu_state_negative_index_rejected():
    with pytest.raises(pydantic.ValidationError):
        RoleMenuState(
            menu_index=-1,
            menu_name="M",
            message_id=100,
            single=False,
            option_role_ids={"X": 1},
        )


def test_menu_state_zero_message_id_rejected():
    with pytest.raises(pydantic.ValidationError):
        RoleMenuState(
            menu_index=0,
            menu_name="M",
            message_id=0,
            single=False,
            option_role_ids={"X": 1},
        )


def test_guild_state_zero_guild_id_rejected():
    with pytest.raises(pydantic.ValidationError):
        RoleMenuGuildState(guild_id=0)


def test_guild_state_defaults():
    gs = RoleMenuGuildState(guild_id=123)
    assert gs.guild_name == ""
    assert gs.role_channel_id is None
    assert gs.role_names == {}
    assert gs.menus == []


def test_guild_state_mutable_defaults_are_independent():
    """Pydantic should give each instance its own copy of mutable defaults."""
    gs1 = RoleMenuGuildState(guild_id=1)
    gs2 = RoleMenuGuildState(guild_id=2)
    gs1.role_names[10] = "Star"
    assert gs2.role_names == {}


def test_guild_state_json_round_trip():
    gs = RoleMenuGuildState(
        guild_id=123,
        guild_name="Test",
        role_channel_id=456,
        role_names={10: "Star", 20: "Fire"},
        menus=[
            RoleMenuState(
                menu_index=0,
                menu_name="Colors",
                message_id=789,
                single=True,
                option_role_ids={"Red": 10, "Blue": 20},
            )
        ],
    )
    data = gs.model_dump(mode="json")
    restored = RoleMenuGuildState.model_validate(data)
    assert restored == gs


# ---------------------------------------------------------------------------- #
#                                    State                                     #
# ---------------------------------------------------------------------------- #


@pytest.fixture()
def role_menus_state_dir(monkeypatch, tmp_path):
    """Monkeypatch role_menus state module to use a temp dir and clear cache."""
    monkeypatch.setattr(role_menus_state, "STATE_DIR", tmp_path)
    role_menus_state._cache.clear()
    return tmp_path


def test_state_load_empty(role_menus_state_dir):
    gs = role_menus_state.load(12345)
    assert gs.guild_id == 12345
    assert gs.menus == []


def test_state_save_and_load(role_menus_state_dir):
    gs = RoleMenuGuildState(
        guild_id=42,
        guild_name="Test Guild",
        role_channel_id=100,
        role_names={10: "Star"},
        menus=[
            RoleMenuState(
                menu_index=0,
                menu_name="Colors",
                message_id=200,
                single=False,
                option_role_ids={"Red": 10},
            )
        ],
    )
    role_menus_state.save(gs)

    # Clear cache to force disk read
    role_menus_state._cache.clear()

    loaded = role_menus_state.load(42)
    assert loaded.guild_id == 42
    assert loaded.guild_name == "Test Guild"
    assert loaded.role_channel_id == 100
    assert len(loaded.menus) == 1
    assert loaded.menus[0].menu_name == "Colors"
    assert loaded.menus[0].option_role_ids == {"Red": 10}


def test_state_load_returns_cached(role_menus_state_dir):
    gs1 = role_menus_state.load(99)
    gs2 = role_menus_state.load(99)
    assert gs1 is gs2


def test_state_yaml_is_human_readable(role_menus_state_dir):
    gs = RoleMenuGuildState(
        guild_id=42,
        guild_name="Test",
        menus=[
            RoleMenuState(
                menu_index=0,
                menu_name="M",
                message_id=100,
                single=False,
                option_role_ids={"X": 1},
            )
        ],
    )
    role_menus_state.save(gs)

    yaml_file = role_menus_state_dir / "role_menus_42.yaml"
    assert yaml_file.exists()
    with open(yaml_file) as f:
        raw = yaml.safe_load(f)
    assert raw["guild_name"] == "Test"
    assert len(raw["menus"]) == 1


def test_state_load_corrupt_yaml_raises(role_menus_state_dir):
    yaml_file = role_menus_state_dir / "role_menus_42.yaml"
    yaml_file.write_text(": : : invalid yaml [[[")
    role_menus_state._cache.clear()

    with pytest.raises(yaml.YAMLError):
        role_menus_state.load(42)


def test_state_load_invalid_data_raises(role_menus_state_dir):
    yaml_file = role_menus_state_dir / "role_menus_42.yaml"
    # guild_id=0 should fail validation
    with open(yaml_file, "w") as f:
        yaml.dump({"guild_id": 0}, f)
    role_menus_state._cache.clear()

    with pytest.raises(pydantic.ValidationError):
        role_menus_state.load(42)


# ---------------------------------------------------------------------------- #
#                                   Commands                                   #
# ---------------------------------------------------------------------------- #


def _sample_guild_state() -> RoleMenuGuildState:
    return RoleMenuGuildState(
        guild_id=100,
        guild_name="Test",
        menus=[
            RoleMenuState(
                menu_index=0,
                menu_name="Colors",
                message_id=200,
                single=False,
                option_role_ids={"Red": 10, "Blue": 20},
            ),
            RoleMenuState(
                menu_index=1,
                menu_name="Roles",
                message_id=300,
                single=True,
                option_role_ids={"Admin": 30},
            ),
        ],
    )


def test_find_menu_state_valid():
    gs = _sample_guild_state()
    result = _find_menu_state(gs, "role_menu:0")
    assert result is not None
    assert result.menu_name == "Colors"


def test_find_menu_state_second_menu():
    gs = _sample_guild_state()
    result = _find_menu_state(gs, "role_menu:1")
    assert result is not None
    assert result.menu_name == "Roles"


def test_find_menu_state_invalid_index():
    gs = _sample_guild_state()
    result = _find_menu_state(gs, "role_menu:abc")
    assert result is None


def test_find_menu_state_missing_index():
    gs = _sample_guild_state()
    result = _find_menu_state(gs, "role_menu:99")
    assert result is None


def test_build_summary_added_only():
    assert _build_summary(["Red"], [], []) == "Added: **Red**"


def test_build_summary_removed_only():
    assert _build_summary([], ["Blue"], []) == "Removed: **Blue**"


def test_build_summary_both():
    result = _build_summary(["Red"], ["Blue"], [])
    assert result == "Added: **Red**. Removed: **Blue**"


def test_build_summary_no_changes():
    assert _build_summary([], [], []) == "No role changes."


def test_build_summary_with_failures():
    result = _build_summary(["Red"], [], ["Admin"])
    assert "Failed (permission error): **Admin**" in result
    assert "Added: **Red**" in result


def test_build_summary_only_failures():
    result = _build_summary([], [], ["Admin"])
    assert result == "Failed (permission error): **Admin**"


def test_build_menu_embed_multi_select():
    menu = RoleMenuConfig(
        name="Colors",
        description="Pick a color",
        options=[RoleMenuOptionConfig(role="Red", description="Red role")],
    )
    embed = build_menu_embed(menu, (255, 0, 0))
    assert embed.title == "Colors"
    assert embed.description == "Pick a color"


def test_build_menu_embed_single_select_with_description():
    menu = RoleMenuConfig(
        name="Gender",
        single=True,
        description="Pick one",
        options=[RoleMenuOptionConfig(role="M", description="Male")],
    )
    embed = build_menu_embed(menu, (0, 255, 0))
    assert embed.title == "Gender (Pick 1)"
    assert "Pick one" in embed.description
    assert "only pick one" in embed.description


def test_build_menu_embed_single_select_no_description():
    menu = RoleMenuConfig(
        name="Gender",
        single=True,
        options=[RoleMenuOptionConfig(role="M", description="Male")],
    )
    embed = build_menu_embed(menu, (0, 0, 255))
    assert embed.title == "Gender (Pick 1)"
    assert "only pick one" in embed.description


def test_build_menu_select_multi():
    menu = RoleMenuConfig(
        name="Colors",
        options=[
            RoleMenuOptionConfig(role="Red", description="Red role"),
            RoleMenuOptionConfig(role="Blue", description="Blue role"),
            RoleMenuOptionConfig(role="Green", description="Green role"),
        ],
    )
    valid_options = [
        ("Red", "Red role", None),
        ("Blue", "Blue role", None),
        ("Green", "Green role", None),
    ]
    select = build_menu_select(0, menu, valid_options, {})
    assert select.min_values == 0
    assert select.max_values == 3


def test_build_menu_select_single():
    menu = RoleMenuConfig(
        name="Gender",
        single=True,
        options=[
            RoleMenuOptionConfig(role="M", description="Male"),
            RoleMenuOptionConfig(role="F", description="Female"),
        ],
    )
    valid_options = [("M", "Male", None), ("F", "Female", None)]
    select = build_menu_select(0, menu, valid_options, {})
    assert select.min_values == 0
    assert select.max_values == 1


def test_build_menu_select_with_emoji():
    emoji = hikari.UnicodeEmoji("🔴")
    emoji_map = {"red_circle": emoji}
    menu = RoleMenuConfig(
        name="Colors",
        options=[
            RoleMenuOptionConfig(role="Red", emoji="red_circle", description="Red role")
        ],
    )
    valid_options = [("Red", "Red role", "red_circle")]
    select = build_menu_select(0, menu, valid_options, emoji_map)
    assert select.custom_id == "role_menu:0"
