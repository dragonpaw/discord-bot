"""Tests for GuildContext helpers and standalone permission functions in context.py."""

from unittest.mock import AsyncMock, Mock

import hikari

from dragonpaw_bot.context import GuildContext, check_guild_perms

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
