"""Tests for the role_menus plugin package."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hikari
import pydantic
import pytest
import yaml

import dragonpaw_bot.plugins.role_menus.state as role_menus_state
from dragonpaw_bot.plugins.role_menus.commands import (
    _apply_role_changes,
    _build_summary,
    _find_menu_state,
    _slugify,
    build_menu_embed,
    build_menu_select,
    handle_role_menu_interaction,
)
from dragonpaw_bot.plugins.role_menus.constants import ROLE_MENU_PREFIX
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


def test_menu_state_empty_slug_rejected():
    with pytest.raises(pydantic.ValidationError):
        RoleMenuState(
            menu_slug="",
            menu_name="M",
            message_id=100,
            single=False,
            option_role_ids={"X": 1},
        )


def test_menu_state_zero_message_id_rejected():
    with pytest.raises(pydantic.ValidationError):
        RoleMenuState(
            menu_slug="m",
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
                menu_slug="colors",
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
    monkeypatch.setattr(role_menus_state.store, "state_dir", tmp_path)
    role_menus_state.store.cache.clear()
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
                menu_slug="colors",
                menu_name="Colors",
                message_id=200,
                single=False,
                option_role_ids={"Red": 10},
            )
        ],
    )
    role_menus_state.save(gs)

    # Clear cache to force disk read
    role_menus_state.store.cache.clear()

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
                menu_slug="m",
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
    role_menus_state.store.cache.clear()

    with pytest.raises(yaml.YAMLError):
        role_menus_state.load(42)


def test_state_load_invalid_data_raises(role_menus_state_dir):
    yaml_file = role_menus_state_dir / "role_menus_42.yaml"
    # guild_id=0 should fail validation
    with open(yaml_file, "w") as f:
        yaml.dump({"guild_id": 0}, f)
    role_menus_state.store.cache.clear()

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
                menu_slug="colors",
                menu_name="Colors",
                message_id=200,
                single=False,
                option_role_ids={"Red": 10, "Blue": 20},
            ),
            RoleMenuState(
                menu_slug="roles",
                menu_name="Roles",
                message_id=300,
                single=True,
                option_role_ids={"Admin": 30},
            ),
        ],
    )


def test_find_menu_state_valid():
    gs = _sample_guild_state()
    result = _find_menu_state(gs, "role_menu:colors")
    assert result is not None
    assert result.menu_name == "Colors"


def test_find_menu_state_second_menu():
    gs = _sample_guild_state()
    result = _find_menu_state(gs, "role_menu:roles")
    assert result is not None
    assert result.menu_name == "Roles"


def test_find_menu_state_empty_slug():
    gs = _sample_guild_state()
    result = _find_menu_state(gs, "role_menu:")
    assert result is None


def test_find_menu_state_missing_slug():
    gs = _sample_guild_state()
    result = _find_menu_state(gs, "role_menu:nonexistent")
    assert result is None


def test_build_summary_added_only():
    assert _build_summary(["Red"], [], []) == "Added: **Red**"


def test_build_summary_removed_only():
    assert _build_summary([], ["Blue"], []) == "Removed: **Blue**"


def test_build_summary_both():
    result = _build_summary(["Red"], ["Blue"], [])
    assert result == "Added: **Red**. Removed: **Blue**"


def test_build_summary_no_changes():
    assert (
        _build_summary([], [], [])
        == "No changes this time! Your roles are just the way you left them 🐾"
    )


def test_build_summary_with_failures():
    result = _build_summary(["Red"], [], ["Admin"])
    assert "Couldn't change: **Admin** (permission error)" in result
    assert "Added: **Red**" in result


def test_build_summary_only_failures():
    result = _build_summary([], [], ["Admin"])
    assert result == "Couldn't change: **Admin** (permission error) — poke an admin! 🐾"


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
    select = build_menu_select("colors", menu, valid_options, {})
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
    select = build_menu_select("gender", menu, valid_options, {})
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
    select = build_menu_select("colors", menu, valid_options, emoji_map)
    assert select.custom_id == "role_menu:colors"


# ---------------------------------------------------------------------------- #
#                                   Slugify                                    #
# ---------------------------------------------------------------------------- #


def test_slugify_spaces_and_case():
    assert _slugify("DM Permission") == "dm-permission"


def test_slugify_all_punctuation_yields_empty_slug():
    """An all-non-alphanumeric menu name slugifies to nothing."""
    assert _slugify("!!! ???") == ""


def test_slugify_empty_slug_menu_is_unreachable():
    """A menu named only in punctuation gets a custom_id no lookup can resolve."""
    custom_id = ROLE_MENU_PREFIX + _slugify("★★★")
    assert _find_menu_state(_sample_guild_state(), custom_id) is None


# ---------------------------------------------------------------------------- #
#                              Interaction handling                            #
# ---------------------------------------------------------------------------- #

MEMBER_ID = hikari.Snowflake(777)
LOG_CHANNEL_ID = hikari.Snowflake(555)


def _interaction_state() -> RoleMenuGuildState:
    return RoleMenuGuildState(
        guild_id=100,
        guild_name="Test Guild",
        role_channel_id=50,
        role_names={10: "Red Role", 20: "Blue Role"},
        menus=[
            RoleMenuState(
                menu_slug="colors",
                menu_name="Colors",
                message_id=200,
                single=False,
                option_role_ids={"Red": 10, "Blue": 20},
            ),
            RoleMenuState(
                menu_slug="pronouns",
                menu_name="Pronouns",
                message_id=300,
                single=True,
                option_role_ids={"They": 30, "She": 40},
            ),
        ],
    )


def _menu_interaction(
    calls: list[str],
    *,
    custom_id: str = "role_menu:colors",
    values: list[str] | None = None,
    member_role_ids: list[int] | None = None,
) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = hikari.Snowflake(100)
    interaction.custom_id = custom_id
    interaction.values = values if values is not None else []
    interaction.user.id = MEMBER_ID
    interaction.member.role_ids = [hikari.Snowflake(r) for r in (member_role_ids or [])]
    interaction.create_initial_response = AsyncMock(
        side_effect=lambda *a, **k: calls.append("initial_response")
    )
    interaction.edit_initial_response = AsyncMock(
        side_effect=lambda *a, **k: calls.append("edit_response")
    )
    bot = interaction.app
    bot.cache.get_guild.return_value = None
    bot.state.return_value = SimpleNamespace(log_channel_id=LOG_CHANNEL_ID)
    bot.rest.add_role_to_member = AsyncMock(
        side_effect=lambda *a, **k: calls.append("add_role")
    )
    bot.rest.remove_role_from_member = AsyncMock(
        side_effect=lambda *a, **k: calls.append("remove_role")
    )
    bot.rest.create_message = AsyncMock(
        side_effect=lambda *a, **k: calls.append("guild_log")
    )
    return interaction


def _menu(guild_state: RoleMenuGuildState, slug: str) -> RoleMenuState:
    return next(m for m in guild_state.menus if m.menu_slug == slug)


# --- _apply_role_changes ---


async def test_apply_role_changes_adds_selected_role():
    calls: list[str] = []
    interaction = _menu_interaction(calls)
    gs = _interaction_state()

    added, removed, failed = await _apply_role_changes(
        interaction, gs, _menu(gs, "colors"), {"Red"}, set()
    )

    assert (added, removed, failed) == (["Red"], [], [])
    interaction.app.rest.add_role_to_member.assert_awaited_once()
    kwargs = interaction.app.rest.add_role_to_member.await_args.kwargs
    assert kwargs["guild"] == hikari.Snowflake(100)
    assert kwargs["user"] == MEMBER_ID
    assert kwargs["role"] == hikari.Snowflake(10)
    interaction.app.rest.remove_role_from_member.assert_not_awaited()


async def test_apply_role_changes_removes_deselected_role():
    calls: list[str] = []
    interaction = _menu_interaction(calls)
    gs = _interaction_state()
    held = {hikari.Snowflake(10), hikari.Snowflake(20)}

    added, removed, failed = await _apply_role_changes(
        interaction, gs, _menu(gs, "colors"), {"Red"}, held
    )

    assert (added, removed, failed) == ([], ["Blue"], [])
    interaction.app.rest.add_role_to_member.assert_not_awaited()
    kwargs = interaction.app.rest.remove_role_from_member.await_args.kwargs
    assert kwargs["role"] == hikari.Snowflake(20)


async def test_apply_role_changes_keeps_role_already_held():
    calls: list[str] = []
    interaction = _menu_interaction(calls)
    gs = _interaction_state()

    added, removed, failed = await _apply_role_changes(
        interaction, gs, _menu(gs, "colors"), {"Red"}, {hikari.Snowflake(10)}
    )

    assert (added, removed, failed) == ([], [], [])
    assert calls == []


async def test_apply_role_changes_single_select_replaces_previous_choice():
    """Picking a new option in a single-select menu drops the old one."""
    calls: list[str] = []
    interaction = _menu_interaction(calls)
    gs = _interaction_state()

    added, removed, failed = await _apply_role_changes(
        interaction, gs, _menu(gs, "pronouns"), {"She"}, {hikari.Snowflake(30)}
    )

    assert added == ["She"]
    assert removed == ["They"]
    assert failed == []
    assert interaction.app.rest.add_role_to_member.await_args.kwargs[
        "role"
    ] == hikari.Snowflake(40)
    assert interaction.app.rest.remove_role_from_member.await_args.kwargs[
        "role"
    ] == hikari.Snowflake(30)


async def test_apply_role_changes_deselect_all_removes_every_held_role():
    calls: list[str] = []
    interaction = _menu_interaction(calls)
    gs = _interaction_state()
    held = {hikari.Snowflake(10), hikari.Snowflake(20)}

    added, removed, failed = await _apply_role_changes(
        interaction, gs, _menu(gs, "colors"), set(), held
    )

    assert added == []
    assert sorted(removed) == ["Blue", "Red"]
    assert failed == []
    assert interaction.app.rest.remove_role_from_member.await_count == 2


async def test_apply_role_changes_forbidden_add_is_reported_not_raised():
    calls: list[str] = []
    interaction = _menu_interaction(calls)
    interaction.app.rest.add_role_to_member.side_effect = hikari.ForbiddenError(
        url="", headers={}, raw_body=b""
    )
    gs = _interaction_state()

    added, removed, failed = await _apply_role_changes(
        interaction, gs, _menu(gs, "colors"), {"Red"}, set()
    )

    assert added == []
    assert removed == []
    # role_names maps the id to the guild's current display name
    assert failed == ["Red Role"]
    interaction.app.rest.create_message.assert_awaited_once()
    kwargs = interaction.app.rest.create_message.await_args.kwargs
    assert kwargs["channel"] == LOG_CHANNEL_ID
    assert "Red Role" in kwargs["content"]


async def test_apply_role_changes_forbidden_remove_is_reported_not_raised():
    calls: list[str] = []
    interaction = _menu_interaction(calls)
    interaction.app.rest.remove_role_from_member.side_effect = hikari.ForbiddenError(
        url="", headers={}, raw_body=b""
    )
    gs = _interaction_state()

    added, removed, failed = await _apply_role_changes(
        interaction, gs, _menu(gs, "colors"), set(), {hikari.Snowflake(20)}
    )

    assert added == []
    assert removed == []
    assert failed == ["Blue Role"]
    content = interaction.app.rest.create_message.await_args.kwargs["content"]
    assert "Blue Role" in content


async def test_apply_role_changes_forbidden_does_not_stop_other_roles():
    calls: list[str] = []
    interaction = _menu_interaction(calls)

    async def only_blue_allowed(*, guild, user, role, reason):
        if role == hikari.Snowflake(10):
            raise hikari.ForbiddenError(url="", headers={}, raw_body=b"")
        calls.append("add_role")

    interaction.app.rest.add_role_to_member.side_effect = only_blue_allowed
    gs = _interaction_state()

    added, removed, failed = await _apply_role_changes(
        interaction, gs, _menu(gs, "colors"), {"Red", "Blue"}, set()
    )

    assert added == ["Blue"]
    assert removed == []
    assert failed == ["Red Role"]


# --- handle_role_menu_interaction ---


async def test_handle_interaction_defers_then_applies_then_summarizes(
    role_menus_state_dir,
):
    role_menus_state.save(_interaction_state())
    calls: list[str] = []
    interaction = _menu_interaction(calls, values=["Red"])

    await handle_role_menu_interaction(interaction)

    assert calls[0] == "initial_response"
    assert calls[1] == "add_role"
    assert calls[-1] == "edit_response"
    defer_kwargs = interaction.create_initial_response.await_args.kwargs
    assert defer_kwargs["response_type"] == hikari.ResponseType.DEFERRED_MESSAGE_CREATE
    assert defer_kwargs["flags"] == hikari.MessageFlag.EPHEMERAL
    assert (
        interaction.edit_initial_response.await_args.kwargs["content"]
        == "Added: **Red**"
    )


async def test_handle_interaction_removes_deselected_role(role_menus_state_dir):
    role_menus_state.save(_interaction_state())
    calls: list[str] = []
    interaction = _menu_interaction(calls, values=["Red"], member_role_ids=[10, 20])

    await handle_role_menu_interaction(interaction)

    interaction.app.rest.remove_role_from_member.assert_awaited_once()
    assert interaction.app.rest.remove_role_from_member.await_args.kwargs[
        "role"
    ] == hikari.Snowflake(20)
    assert (
        interaction.edit_initial_response.await_args.kwargs["content"]
        == "Removed: **Blue**"
    )


async def test_handle_interaction_empty_selection_clears_roles(role_menus_state_dir):
    role_menus_state.save(_interaction_state())
    calls: list[str] = []
    interaction = _menu_interaction(calls, values=[], member_role_ids=[10, 20])

    await handle_role_menu_interaction(interaction)

    assert interaction.app.rest.remove_role_from_member.await_count == 2
    assert "Removed:" in interaction.edit_initial_response.await_args.kwargs["content"]


async def test_handle_interaction_no_changes_reports_no_changes(role_menus_state_dir):
    role_menus_state.save(_interaction_state())
    calls: list[str] = []
    interaction = _menu_interaction(calls, values=["Red"], member_role_ids=[10])

    await handle_role_menu_interaction(interaction)

    assert "add_role" not in calls
    assert "remove_role" not in calls
    assert (
        "No changes" in interaction.edit_initial_response.await_args.kwargs["content"]
    )


async def test_handle_interaction_unknown_slug_responds_without_touching_roles(
    role_menus_state_dir,
):
    role_menus_state.save(_interaction_state())
    calls: list[str] = []
    interaction = _menu_interaction(calls, custom_id="role_menu:gone", values=["Red"])

    await handle_role_menu_interaction(interaction)

    kwargs = interaction.create_initial_response.await_args.kwargs
    assert kwargs["response_type"] == hikari.ResponseType.MESSAGE_CREATE
    assert kwargs["flags"] == hikari.MessageFlag.EPHEMERAL
    assert "don't recognize" in kwargs["content"]
    interaction.edit_initial_response.assert_not_awaited()
    interaction.app.rest.add_role_to_member.assert_not_awaited()


async def test_handle_interaction_no_menus_in_state_responds_gracefully(
    role_menus_state_dir,
):
    calls: list[str] = []
    interaction = _menu_interaction(calls, values=["Red"])

    await handle_role_menu_interaction(interaction)

    kwargs = interaction.create_initial_response.await_args.kwargs
    assert kwargs["response_type"] == hikari.ResponseType.MESSAGE_CREATE
    assert "outdated" in kwargs["content"]
    interaction.app.rest.add_role_to_member.assert_not_awaited()


async def test_handle_interaction_without_member_does_not_respond(
    role_menus_state_dir,
):
    role_menus_state.save(_interaction_state())
    calls: list[str] = []
    interaction = _menu_interaction(calls, values=["Red"])
    interaction.member = None

    await handle_role_menu_interaction(interaction)

    assert calls == []


async def test_handle_interaction_expired_defer_skips_role_changes(
    role_menus_state_dir,
):
    role_menus_state.save(_interaction_state())
    calls: list[str] = []
    interaction = _menu_interaction(calls, values=["Red"])
    interaction.create_initial_response.side_effect = hikari.NotFoundError(
        url="", headers={}, raw_body=b""
    )

    await handle_role_menu_interaction(interaction)

    interaction.app.rest.add_role_to_member.assert_not_awaited()
    interaction.edit_initial_response.assert_not_awaited()


async def test_handle_interaction_summary_failure_does_not_raise(role_menus_state_dir):
    role_menus_state.save(_interaction_state())
    calls: list[str] = []
    interaction = _menu_interaction(calls, values=["Red"])
    interaction.edit_initial_response.side_effect = hikari.HTTPError("boom")

    await handle_role_menu_interaction(interaction)

    interaction.app.rest.add_role_to_member.assert_awaited_once()
