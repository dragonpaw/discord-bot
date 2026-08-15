import datetime
from unittest.mock import AsyncMock, MagicMock, Mock

import hikari

from dragonpaw_bot.plugins.birthdays import config as birthdays_config
from dragonpaw_bot.plugins.birthdays import cron as birthdays_cron
from dragonpaw_bot.plugins.birthdays import state as birthdays_state
from dragonpaw_bot.plugins.birthdays.commands import _days_until_birthday
from dragonpaw_bot.plugins.birthdays.models import BirthdayEntry, BirthdayGuildState

# ---------------------------------------------------------------------------- #
#                          process_guild_birthdays                             #
# ---------------------------------------------------------------------------- #


async def test_cron_processes_entry_without_timezone(tmp_path, monkeypatch):
    """Entries with no timezone default to UTC — they must be announced, not
    silently skipped forever."""
    monkeypatch.setattr(birthdays_state.store, "state_dir", tmp_path)
    birthdays_state.store.cache.clear()

    today = datetime.datetime.now(tz=datetime.UTC).date()
    st = BirthdayGuildState(
        guild_id=1,
        guild_name="TestGuild",
        birthdays={
            42: BirthdayEntry(
                user_id=42, month=today.month, day=today.day, timezone=None
            )
        },
    )
    birthdays_state.save(st)

    # Pin the clock to the user's local midnight so the hourly gate opens.
    monkeypatch.setattr(birthdays_cron.commands, "user_local_hour", lambda _e: 0)

    member = Mock()
    member.display_name = "Birthday Person"
    monkeypatch.setattr(
        birthdays_cron.utils, "guild_member", AsyncMock(return_value=member)
    )
    announced: list[int] = []

    async def _fake_announce(_gc, mem, entry, _cfg):
        announced.append(entry.user_id)

    monkeypatch.setattr(birthdays_cron, "announce_birthday", _fake_announce)

    gc = MagicMock()
    gc.guild_id = hikari.Snowflake(1)
    gc.name = "TestGuild"
    gc.logger = MagicMock()

    await birthdays_cron.process_guild_birthdays(gc)

    assert announced == [42]
    birthdays_state.store.cache.clear()
    assert birthdays_state.load(1).birthdays[42].last_announced == today


# ---------------------------------------------------------------------------- #
#                        config panel authorization                            #
# ---------------------------------------------------------------------------- #


def _config_interaction(permissions: hikari.Permissions) -> MagicMock:
    interaction = MagicMock()
    interaction.custom_id = "birthday_cfg:birthday_role"
    interaction.guild_id = hikari.Snowflake(1)
    interaction.user.id = hikari.Snowflake(42)  # not the guild owner
    interaction.member.permissions = permissions
    interaction.values = []
    interaction.create_initial_response = AsyncMock()
    interaction.edit_initial_response = AsyncMock()
    bot = interaction.app
    guild = Mock()
    guild.id = hikari.Snowflake(1)
    guild.name = "TestGuild"
    guild.owner_id = hikari.Snowflake(999)
    bot.rest.fetch_guild = AsyncMock(return_value=guild)
    return interaction


async def test_config_gate_rejects_non_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(birthdays_state.store, "state_dir", tmp_path)
    birthdays_state.store.cache.clear()
    interaction = _config_interaction(hikari.Permissions.NONE)

    await birthdays_config.handle_config_interaction(interaction)

    content = interaction.edit_initial_response.call_args.kwargs["content"]
    assert "server" in content.lower()


async def test_config_gate_admits_manage_guild_member(tmp_path, monkeypatch):
    """The panel must admit the same members as the guild_owner_only command hook
    (MANAGE_GUILD / ADMINISTRATOR), not just the literal guild owner."""
    monkeypatch.setattr(birthdays_state.store, "state_dir", tmp_path)
    birthdays_state.store.cache.clear()
    interaction = _config_interaction(hikari.Permissions.MANAGE_GUILD)

    responded: list[str] = []

    async def _fake_respond(_bot, _interaction, _guild_id, _cfg, footer=None):
        responded.append(footer or "")

    monkeypatch.setattr(birthdays_config, "_respond_config_embed", _fake_respond)

    await birthdays_config.handle_config_interaction(interaction)

    assert responded, "admin with MANAGE_GUILD should reach the config flow"
    interaction.edit_initial_response.assert_not_called()


# ---------------------------------------------------------------------------- #
#                          _days_until_birthday                                #
# ---------------------------------------------------------------------------- #


def test_days_until_birthday_today_is_zero():
    today = datetime.date(2026, 6, 15)
    assert _days_until_birthday(6, 15, today) == 0


def test_days_until_birthday_upcoming():
    today = datetime.date(2026, 6, 15)
    assert _days_until_birthday(6, 20, today) == 5


def test_days_until_birthday_wraps_to_next_year():
    today = datetime.date(2026, 12, 31)
    assert _days_until_birthday(1, 1, today) == 1


def test_days_until_birthday_feb29_non_leap_maps_to_mar1():
    # 2026 is not a leap year: Feb 29 birthdays celebrate Mar 1 (as
    # is_birthday_on_date does), never Feb 28.
    today = datetime.date(2026, 2, 28)
    assert _days_until_birthday(2, 29, today) == 1
