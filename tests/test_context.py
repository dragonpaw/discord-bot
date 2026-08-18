"""Tests for GuildContext helpers and standalone permission functions in context.py."""

from unittest.mock import AsyncMock, Mock

import hikari
import pytest

from dragonpaw_bot import journal
from dragonpaw_bot.context import (
    PRIVATE_CHANNEL_USER_PERMS,
    GuildContext,
    actor_name,
    check_guild_perms,
    is_guild_admin,
    role_list_label,
)

GUILD_ID = hikari.Snowflake(50)
BOT_USER_ID = hikari.Snowflake(1)


# ---------------------------------------------------------------------------- #
#                           check_guild_perms                                   #
# ---------------------------------------------------------------------------- #


def _mock_bot_for_guild(
    *,
    role_perms: hikari.Permissions = hikari.Permissions.NONE,
    extra_role_perms: hikari.Permissions | None = None,
) -> Mock:
    """Create a minimal DragonpawBot mock for check_guild_perms tests.

    GUILD_ID doubles as the @everyone role ID (matching Discord's convention).
    If extra_role_perms is given, the bot member gets one extra role with those perms.
    """
    bot = Mock()
    bot.user_id = BOT_USER_ID

    member = Mock(spec=hikari.Member)
    member.id = BOT_USER_ID

    everyone_role = Mock(spec=hikari.Role)
    everyone_role.id = GUILD_ID
    everyone_role.permissions = role_perms

    role_map: dict[hikari.Snowflake, Mock] = {everyone_role.id: everyone_role}

    if extra_role_perms is not None:
        extra_role = Mock(spec=hikari.Role)
        extra_role.id = hikari.Snowflake(999)
        extra_role.permissions = extra_role_perms
        role_map[extra_role.id] = extra_role
        member.role_ids = [extra_role.id]
    else:
        member.role_ids = []

    bot.cache.get_member = Mock(return_value=member)
    bot.rest.fetch_member = AsyncMock(return_value=member)
    bot.cache.get_roles_view_for_guild = Mock(return_value=role_map)

    return bot


REQUIRED = {hikari.Permissions.KICK_MEMBERS: "Kick Members"}


async def test_check_guild_perms_bot_has_permission_via_role():
    bot = _mock_bot_for_guild(extra_role_perms=hikari.Permissions.KICK_MEMBERS)
    result = await check_guild_perms(bot, GUILD_ID, REQUIRED)
    assert result == []


async def test_check_guild_perms_bot_lacks_permission():
    bot = _mock_bot_for_guild(role_perms=hikari.Permissions.NONE)
    result = await check_guild_perms(bot, GUILD_ID, REQUIRED)
    assert result == ["Kick Members"]


async def test_check_guild_perms_administrator_bypass():
    bot = _mock_bot_for_guild(role_perms=hikari.Permissions.ADMINISTRATOR)
    result = await check_guild_perms(bot, GUILD_ID, REQUIRED)
    assert result == []


async def test_check_guild_perms_cache_miss_falls_back_to_rest():
    bot = _mock_bot_for_guild(role_perms=hikari.Permissions.KICK_MEMBERS)
    # Simulate cache miss — should fall through to REST fetch
    bot.cache.get_member = Mock(return_value=None)
    result = await check_guild_perms(bot, GUILD_ID, REQUIRED)
    bot.rest.fetch_member.assert_called_once_with(GUILD_ID, BOT_USER_ID)
    assert result == []


async def test_check_guild_perms_no_everyone_role_in_map():
    """When @everyone isn't in the role map, perms default to NONE (no crash)."""
    bot = _mock_bot_for_guild()
    # Remove the @everyone entry from the role map
    bot.cache.get_roles_view_for_guild = Mock(return_value={})
    result = await check_guild_perms(bot, GUILD_ID, REQUIRED)
    assert "Kick Members" in result


# ---------------------------------------------------------------------------- #
#                       GuildContext.delete_channel                             #
# ---------------------------------------------------------------------------- #


def _make_gc(*, log_channel_id: hikari.Snowflake | None = None) -> GuildContext:
    bot = Mock()
    bot.rest.delete_channel = AsyncMock()
    bot.rest.create_message = AsyncMock()
    return GuildContext(
        bot=bot,
        guild_id=GUILD_ID,
        name="Test Guild",
        log_channel_id=log_channel_id,
    )


CHANNEL_ID = hikari.Snowflake(42)


async def test_delete_channel_success():
    gc = _make_gc()
    await gc.delete_channel(CHANNEL_ID)
    gc.bot.rest.delete_channel.assert_called_once_with(CHANNEL_ID)


async def test_delete_channel_not_found_is_silent():
    gc = _make_gc(log_channel_id=hikari.Snowflake(99))
    gc.bot.rest.delete_channel = AsyncMock(
        side_effect=hikari.NotFoundError(url="", headers={}, raw_body=b"")
    )
    await gc.delete_channel(CHANNEL_ID)
    # NotFoundError should NOT post to the log channel
    gc.bot.rest.create_message.assert_not_called()


async def test_delete_channel_forbidden_logs_to_staff():
    gc = _make_gc(log_channel_id=hikari.Snowflake(99))
    gc.bot.rest.delete_channel = AsyncMock(
        side_effect=hikari.ForbiddenError(url="", headers={}, raw_body=b"")
    )
    await gc.delete_channel(CHANNEL_ID)
    gc.bot.rest.create_message.assert_called_once()
    content = gc.bot.rest.create_message.call_args.kwargs["content"]
    assert "Manage Channels" in content


async def test_delete_channel_http_error_logs_to_staff():
    gc = _make_gc(log_channel_id=hikari.Snowflake(99))
    gc.bot.rest.delete_channel = AsyncMock(
        side_effect=hikari.HTTPResponseError(
            url="", headers={}, raw_body=b"", status=500, message="oops"
        )
    )
    await gc.delete_channel(CHANNEL_ID)
    gc.bot.rest.create_message.assert_called_once()


# ---------------------------------------------------------------------------- #
#                    GuildContext.create_private_channel                        #
# ---------------------------------------------------------------------------- #


def _make_gc_with_create(
    *,
    user_id: int = BOT_USER_ID,
    log_channel_id: hikari.Snowflake | None = None,
    create_side_effect: Exception | None = None,
) -> GuildContext:
    bot = Mock()
    bot.user_id = hikari.Snowflake(user_id)
    bot.rest.create_message = AsyncMock()
    if create_side_effect:
        bot.rest.create_guild_text_channel = AsyncMock(side_effect=create_side_effect)
    else:
        channel = Mock(spec=hikari.GuildTextChannel)
        channel.id = hikari.Snowflake(77)
        bot.rest.create_guild_text_channel = AsyncMock(return_value=channel)
    return GuildContext(
        bot=bot,
        guild_id=GUILD_ID,
        name="Test Guild",
        log_channel_id=log_channel_id,
    )


async def test_create_private_channel_denies_everyone():
    gc = _make_gc_with_create()
    await gc.create_private_channel("validate-alice", user_ids=[hikari.Snowflake(200)])

    _, kwargs = gc.bot.rest.create_guild_text_channel.call_args
    overwrites: list[hikari.PermissionOverwrite] = kwargs["permission_overwrites"]

    everyone_ow = next(
        (
            o
            for o in overwrites
            if o.id == GUILD_ID and o.type == hikari.PermissionOverwriteType.ROLE
        ),
        None,
    )
    assert everyone_ow is not None
    assert hikari.Permissions.VIEW_CHANNEL in everyone_ow.deny


async def test_create_private_channel_grants_user():
    gc = _make_gc_with_create()
    user_id = hikari.Snowflake(200)
    await gc.create_private_channel("validate-alice", user_ids=[user_id])

    _, kwargs = gc.bot.rest.create_guild_text_channel.call_args
    overwrites: list[hikari.PermissionOverwrite] = kwargs["permission_overwrites"]

    user_ow = next(
        (
            o
            for o in overwrites
            if o.id == user_id and o.type == hikari.PermissionOverwriteType.MEMBER
        ),
        None,
    )
    assert user_ow is not None
    assert hikari.Permissions.VIEW_CHANNEL in user_ow.allow
    assert hikari.Permissions.SEND_MESSAGES in user_ow.allow
    assert hikari.Permissions.READ_MESSAGE_HISTORY in user_ow.allow
    assert hikari.Permissions.ATTACH_FILES in user_ow.allow
    assert user_ow.allow == PRIVATE_CHANNEL_USER_PERMS


async def test_create_private_channel_grants_extra_role():
    gc = _make_gc_with_create()
    role_id = hikari.Snowflake(300)
    await gc.create_private_channel(
        "validate-alice", user_ids=[], extra_roles=[role_id]
    )

    _, kwargs = gc.bot.rest.create_guild_text_channel.call_args
    overwrites: list[hikari.PermissionOverwrite] = kwargs["permission_overwrites"]

    role_ow = next(
        (
            o
            for o in overwrites
            if o.id == role_id and o.type == hikari.PermissionOverwriteType.ROLE
        ),
        None,
    )
    assert role_ow is not None
    assert hikari.Permissions.VIEW_CHANNEL in role_ow.allow
    assert hikari.Permissions.ATTACH_FILES in role_ow.allow
    assert role_ow.allow == PRIVATE_CHANNEL_USER_PERMS


def test_private_channel_user_perms_constant_contents():
    """Lock in the contract of PRIVATE_CHANNEL_USER_PERMS so a future cleanup
    PR can't silently drop a flag — that's the exact bug ce60ddf fixed."""
    expected = (
        hikari.Permissions.VIEW_CHANNEL
        | hikari.Permissions.SEND_MESSAGES
        | hikari.Permissions.READ_MESSAGE_HISTORY
        | hikari.Permissions.ATTACH_FILES
    )
    assert expected == PRIVATE_CHANNEL_USER_PERMS


async def test_create_private_channel_with_category():
    gc = _make_gc_with_create()
    await gc.create_private_channel("validate-alice", user_ids=[], category_id=555)

    _, kwargs = gc.bot.rest.create_guild_text_channel.call_args
    assert kwargs["category"] == hikari.Snowflake(555)


async def test_create_private_channel_no_category_passes_undefined():
    gc = _make_gc_with_create()
    await gc.create_private_channel("validate-alice", user_ids=[], category_id=None)

    _, kwargs = gc.bot.rest.create_guild_text_channel.call_args
    assert kwargs["category"] is hikari.UNDEFINED


async def test_create_private_channel_forbidden_logs_and_reraises():
    gc = _make_gc_with_create(
        log_channel_id=hikari.Snowflake(99),
        create_side_effect=hikari.ForbiddenError(url="", headers={}, raw_body=b""),
    )
    try:
        await gc.create_private_channel("validate-alice", user_ids=[])
    except hikari.ForbiddenError:
        pass
    else:
        raise AssertionError("Expected ForbiddenError to be re-raised")

    gc.bot.rest.create_message.assert_called_once()
    content = gc.bot.rest.create_message.call_args.kwargs["content"]
    assert "Manage Channels" in content


# ---------------------------------------------------------------------------- #
#                              is_guild_admin                                  #
# ---------------------------------------------------------------------------- #


def test_is_guild_admin_none_member():
    assert is_guild_admin(None) is False


def test_is_guild_admin_manage_guild():
    member = Mock()
    member.permissions = hikari.Permissions.MANAGE_GUILD
    assert is_guild_admin(member) is True


def test_is_guild_admin_administrator():
    member = Mock()
    member.permissions = hikari.Permissions.ADMINISTRATOR
    assert is_guild_admin(member) is True


def test_is_guild_admin_plain_member():
    member = Mock()
    member.permissions = hikari.Permissions.SEND_MESSAGES
    assert is_guild_admin(member) is False


def test_role_list_label_names_roles():
    assert role_list_label(["A", "B"]) == "one of the **A**, **B** roles"


def test_role_list_label_empty_is_owner_only():
    assert role_list_label([]) == "server owner status"


def test_actor_name_prefers_member_display_name():
    ctx = Mock()
    ctx.member.display_name = "Guild Nick"
    ctx.user.display_name = "Global Name"
    assert actor_name(ctx) == "Guild Nick"


def test_actor_name_falls_back_to_user():
    ctx = Mock()
    ctx.member = None
    ctx.user.display_name = "Global Name"
    assert actor_name(ctx) == "Global Name"


# ---------------------------------------------------------------------------- #
#                        GuildContext.log journal flag                          #
# ---------------------------------------------------------------------------- #


@pytest.fixture
def journal_store(tmp_path, monkeypatch):
    monkeypatch.setattr(journal.store, "state_dir", tmp_path)
    journal.store.cache.clear()
    return journal.store


def _journal_gc(*, log_channel_id: hikari.Snowflake | None) -> GuildContext:
    bot = Mock()
    bot.rest.create_message = AsyncMock()
    return GuildContext(
        bot=bot,
        guild_id=GUILD_ID,
        name="Test Guild",
        log_channel_id=log_channel_id,
    )


def _subject(user_id: int = 7, name: str = "Vee") -> Mock:
    member = Mock()
    member.id = user_id
    member.display_name = name
    return member


async def test_log_records_journal_entry(journal_store):
    gc = _journal_gc(log_channel_id=hikari.Snowflake(99))
    await gc.log(
        "🎫 opened a ticket", journal_kind="ticket_opened", journal_user=_subject()
    )
    entries = journal.entries_for(int(GUILD_ID), 7)
    assert [e.kind for e in entries] == ["ticket_opened"]
    assert entries[0].summary == "🎫 opened a ticket"
    assert entries[0].user_name == "Vee"


async def test_journal_summary_overrides_message(journal_store):
    gc = _journal_gc(log_channel_id=hikari.Snowflake(99))
    await gc.log(
        "🎫 *happy flap* long chatty staff message",
        journal_kind="ticket_opened",
        journal_user=_subject(),
        journal_summary="Opened a ticket",
    )
    assert journal.entries_for(int(GUILD_ID), 7)[0].summary == "Opened a ticket"


async def test_no_log_channel_means_no_journal_entry(journal_store):
    gc = _journal_gc(log_channel_id=None)
    await gc.log("🎫 opened", journal_kind="ticket_opened", journal_user=_subject())
    assert journal.entries_for(int(GUILD_ID), 7) == []


async def test_http_error_still_records_journal_entry(journal_store):
    gc = _journal_gc(log_channel_id=hikari.Snowflake(99))
    gc.bot.rest.create_message = AsyncMock(
        side_effect=hikari.HTTPResponseError(
            url="", headers={}, raw_body=b"", status=500, message="oops"
        )
    )
    await gc.log("🎫 opened", journal_kind="ticket_opened", journal_user=_subject())
    assert len(journal.entries_for(int(GUILD_ID), 7)) == 1


async def test_plain_log_records_nothing(journal_store):
    gc = _journal_gc(log_channel_id=hikari.Snowflake(99))
    await gc.log("⚙️ just a config change")
    assert journal.load(int(GUILD_ID)).entries == []


async def test_kind_without_user_raises(journal_store):
    gc = _journal_gc(log_channel_id=hikari.Snowflake(99))
    with pytest.raises(ValueError):
        await gc.log("x", journal_kind="ticket_opened")


async def test_user_without_kind_raises(journal_store):
    gc = _journal_gc(log_channel_id=hikari.Snowflake(99))
    with pytest.raises(ValueError):
        await gc.log("x", journal_user=_subject())
