"""Tests for the button channel."""

import datetime
from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest

import dragonpaw_bot.plugins.activity.state as activity_state
import dragonpaw_bot.plugins.birthdays.state as birthday_state
import dragonpaw_bot.plugins.subday.state as subday_state
import dragonpaw_bot.plugins.tickets.state as tickets_state
from dragonpaw_bot import buttons
from dragonpaw_bot.bot import _INTERACTION_ROUTES
from dragonpaw_bot.plugins.activity.models import ActivityGuildMeta
from dragonpaw_bot.plugins.birthdays.models import (
    BirthdayGuildConfig,
    BirthdayGuildState,
)
from dragonpaw_bot.plugins.subday.models import SubDayGuildState
from dragonpaw_bot.plugins.tickets.models import TicketGuildState
from dragonpaw_bot.structs import ButtonEntry, ButtonSpec, GuildState

GUILD_ID = 424242
CHANNEL_ID = 555000


# ---------------------------------------------------------------------------- #
#                                  The registry                                 #
# ---------------------------------------------------------------------------- #


def test_entry_keys_are_unique():
    keys = [entry.key for entry in buttons._ENTRIES]
    assert len(keys) == len(set(keys))


def test_every_entry_has_one_or_two_buttons():
    for entry in buttons._ENTRIES:
        assert 1 <= len(entry.buttons) <= 2, entry.key


def test_custom_ids_are_unique_across_entries():
    ids = [b.custom_id for entry in buttons._ENTRIES for b in entry.buttons]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "custom_id",
    [b.custom_id for entry in buttons._ENTRIES for b in entry.buttons],
)
def test_every_button_custom_id_is_routed(custom_id):
    """A card whose button has no handler would only surface in production as
    an 'Unhandled interaction' error, so pin it down here."""
    assert any(custom_id.startswith(prefix) for prefix, _, _ in _INTERACTION_ROUTES)


def test_entries_have_distinct_colors():
    colors = [entry.color for entry in buttons._ENTRIES]
    assert len(colors) == len(set(colors))


# ---------------------------------------------------------------------------- #
#                                  Availability                                 #
# ---------------------------------------------------------------------------- #


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    """Point every plugin's state at a temp dir, with caches cleared."""
    for module in (tickets_state, birthday_state, subday_state):
        monkeypatch.setattr(module.store, "state_dir", tmp_path)
        module.store.cache.clear()
    monkeypatch.setattr(activity_state, "STATE_DIR", tmp_path)
    activity_state._config_cache.clear()
    return tmp_path


def _entry(key: str) -> ButtonEntry:
    return next(e for e in buttons._ENTRIES if e.key == key)


def test_tickets_hidden_without_staff_role(isolated_state):
    tickets_state.save(TicketGuildState(guild_id=GUILD_ID))
    assert _entry("tickets").is_available(GUILD_ID) is False


def test_tickets_shown_with_staff_role(isolated_state):
    tickets_state.save(TicketGuildState(guild_id=GUILD_ID, staff_role_id=77))
    assert _entry("tickets").is_available(GUILD_ID) is True


def test_birthdays_hidden_without_announcement_channel(isolated_state):
    birthday_state.save(BirthdayGuildState(guild_id=GUILD_ID))
    assert _entry("birthdays").is_available(GUILD_ID) is False


def test_birthdays_shown_with_announcement_channel(isolated_state):
    birthday_state.save(
        BirthdayGuildState(
            guild_id=GUILD_ID,
            config=BirthdayGuildConfig(announcement_channel="celebrations"),
        )
    )
    assert _entry("birthdays").is_available(GUILD_ID) is True


def test_subday_hidden_when_unconfigured(isolated_state):
    assert _entry("subday").is_available(GUILD_ID) is False


def test_subday_shown_once_state_exists(isolated_state):
    subday_state.save(SubDayGuildState(guild_id=GUILD_ID))
    assert _entry("subday").is_available(GUILD_ID) is True


def test_activity_hidden_when_unconfigured(isolated_state):
    assert _entry("activity").is_available(GUILD_ID) is False


def test_activity_shown_once_configured(isolated_state):
    activity_state.save_config(ActivityGuildMeta(guild_id=GUILD_ID))
    assert _entry("activity").is_available(GUILD_ID) is True


def test_available_entries_filters_and_preserves_order(isolated_state):
    tickets_state.save(TicketGuildState(guild_id=GUILD_ID, staff_role_id=77))
    subday_state.save(SubDayGuildState(guild_id=GUILD_ID))

    keys = [e.key for e in buttons.available_entries(GUILD_ID)]
    assert keys == ["tickets", "subday"]


def test_available_entries_empty_for_unconfigured_guild(isolated_state):
    assert buttons.available_entries(GUILD_ID) == []


# ---------------------------------------------------------------------------- #
#                                   Rendering                                   #
# ---------------------------------------------------------------------------- #


def test_build_embed_uses_entry_fields():
    entry = ButtonEntry(
        key="demo",
        title="Title",
        description="Description",
        color=hikari.Color(0x123456),
        buttons=(ButtonSpec(custom_id="demo_go", label="Go", emoji="🐉"),),
        is_available=lambda _: True,
    )
    embed = buttons._build_embed(entry)
    assert embed.title == "Title"
    assert embed.description == "Description"
    assert embed.color == hikari.Color(0x123456)


def test_build_row_has_one_button_per_spec():
    entry = ButtonEntry(
        key="demo",
        title="Title",
        description="Description",
        color=hikari.Color(0x123456),
        buttons=(
            ButtonSpec(custom_id="demo_go", label="Go", emoji="🐉"),
            ButtonSpec(custom_id="demo_why", label="Why?", emoji="❓"),
        ),
        is_available=lambda _: True,
    )
    row = buttons._build_row(entry)
    assert [c.custom_id for c in row.components] == ["demo_go", "demo_why"]
    assert [c.label for c in row.components] == ["Go", "Why?"]


def _guild_context(button_channel_id: int | None) -> MagicMock:
    """A GuildContext stand-in wired for post_buttons()."""
    gc = MagicMock()
    gc.guild_id = hikari.Snowflake(GUILD_ID)
    gc.name = "Test Guild"
    gc.logger = MagicMock()
    gc.log = AsyncMock()
    gc.bot.cache.get_guild_channel.return_value = MagicMock(name="buttons")
    gc.bot.rest.create_message = AsyncMock()
    gc.state.return_value = GuildState(
        id=hikari.Snowflake(GUILD_ID),
        name="Test Guild",
        config_url="",
        config_last=datetime.datetime.now(tz=datetime.UTC),
        button_channel_id=hikari.Snowflake(button_channel_id)
        if button_channel_id
        else None,
    )
    return gc


@pytest.fixture()
def no_perm_problems(monkeypatch):
    """ChannelContext built by post_buttons reports full permissions and a
    no-op wipe."""
    cc = MagicMock()
    cc.channel_name = "buttons"
    cc.check_perms = AsyncMock(return_value=[])
    cc.delete_my_messages = AsyncMock()
    monkeypatch.setattr(buttons, "_channel_context", lambda gc, channel_id: cc)
    return cc


async def test_post_buttons_without_a_channel_warns(isolated_state):
    gc = _guild_context(None)
    warnings = await buttons.post_buttons(gc)
    assert len(warnings) == 1
    assert "config buttons channel" in warnings[0]


async def test_post_buttons_sends_one_message_per_entry(
    isolated_state, no_perm_problems
):
    tickets_state.save(TicketGuildState(guild_id=GUILD_ID, staff_role_id=77))
    subday_state.save(SubDayGuildState(guild_id=GUILD_ID))

    gc = _guild_context(CHANNEL_ID)
    warnings = await buttons.post_buttons(gc)

    assert warnings == []
    no_perm_problems.delete_my_messages.assert_awaited_once()
    assert gc.bot.rest.create_message.await_count == 2
    titles = [
        call.kwargs["embed"].title
        for call in gc.bot.rest.create_message.await_args_list
    ]
    assert titles == [_entry("tickets").title, _entry("subday").title]


async def test_post_buttons_posts_a_note_when_nothing_is_configured(
    isolated_state, no_perm_problems
):
    gc = _guild_context(CHANNEL_ID)
    warnings = await buttons.post_buttons(gc)

    assert len(warnings) == 1
    gc.bot.rest.create_message.assert_awaited_once()
    assert (
        gc.bot.rest.create_message.await_args.kwargs["content"] == buttons._EMPTY_NOTE
    )


async def test_post_buttons_bails_on_missing_permissions(isolated_state, monkeypatch):
    tickets_state.save(TicketGuildState(guild_id=GUILD_ID, staff_role_id=77))

    cc = MagicMock()
    cc.channel_name = "buttons"
    cc.check_perms = AsyncMock(return_value=["Manage Messages"])
    cc.delete_my_messages = AsyncMock()
    monkeypatch.setattr(buttons, "_channel_context", lambda gc, channel_id: cc)

    gc = _guild_context(CHANNEL_ID)
    warnings = await buttons.post_buttons(gc)

    assert len(warnings) == 1
    assert "Manage Messages" in warnings[0]
    cc.delete_my_messages.assert_not_awaited()
    gc.bot.rest.create_message.assert_not_awaited()
