from unittest.mock import AsyncMock, Mock

import hikari

from dragonpaw_bot.utils import (
    check_channel_perms,
    has_any_role_permission,
    has_permission,
)

# ---------------------------------------------------------------------------- #
#                              has_permission                                   #
# ---------------------------------------------------------------------------- #


def _mock_member(user_id: int, roles: list[str] | None = None) -> hikari.Member:
    """Create a mock Member with given ID and optional role names."""
    member = Mock(spec=hikari.Member)
    member.id = hikari.Snowflake(user_id)
    if roles:
        mock_roles = []
        for name in roles:
            r = Mock(spec=hikari.Role)
            r.name = name
            mock_roles.append(r)
        member.get_roles.return_value = mock_roles
    else:
        member.get_roles.return_value = []
    return member


def _mock_guild(owner_id: int) -> hikari.Guild:
    guild = Mock(spec=hikari.Guild)
    guild.owner_id = hikari.Snowflake(owner_id)
    return guild


def test_has_permission_guild_owner_always_passes():
    guild = _mock_guild(owner_id=100)
    member = _mock_member(user_id=100)
    assert has_permission(guild, member, "SomeRole") is True


def test_has_permission_guild_owner_passes_with_no_role():
    guild = _mock_guild(owner_id=100)
    member = _mock_member(user_id=100)
    assert has_permission(guild, member, None) is True


def test_has_permission_member_with_matching_role():
    guild = _mock_guild(owner_id=999)
    member = _mock_member(user_id=200, roles=["Admin", "Moderator"])
    assert has_permission(guild, member, "Moderator") is True


def test_has_permission_member_without_matching_role():
    guild = _mock_guild(owner_id=999)
    member = _mock_member(user_id=200, roles=["Member"])
    assert has_permission(guild, member, "Admin") is False


def test_has_permission_none_role_non_owner_fails():
    guild = _mock_guild(owner_id=999)
    member = _mock_member(user_id=200, roles=["Admin"])
    assert has_permission(guild, member, None) is False


# ---------------------------------------------------------------------------- #
#                        has_any_role_permission                                #
# ---------------------------------------------------------------------------- #


def test_has_any_role_permission_owner_bypass():
    guild = _mock_guild(owner_id=100)
    member = _mock_member(user_id=100)
    assert has_any_role_permission(guild, member, ["RoleA", "RoleB"]) is True


def test_has_any_role_permission_match_one_of_many():
    guild = _mock_guild(owner_id=999)
    member = _mock_member(user_id=200, roles=["RoleB"])
    assert has_any_role_permission(guild, member, ["RoleA", "RoleB"]) is True


def test_has_any_role_permission_no_match():
    guild = _mock_guild(owner_id=999)
    member = _mock_member(user_id=200, roles=["RoleC"])
    assert has_any_role_permission(guild, member, ["RoleA", "RoleB"]) is False


def test_has_any_role_permission_empty_list_non_owner():
    guild = _mock_guild(owner_id=999)
    member = _mock_member(user_id=200, roles=["Admin"])
    assert has_any_role_permission(guild, member, []) is False


# ---------------------------------------------------------------------------- #
#                           check_channel_perms                                #
# ---------------------------------------------------------------------------- #


def _mock_bot(
    *,
    role_perms: hikari.Permissions = hikari.Permissions.NONE,
    fetch_channel_side_effect: Exception | None = None,
) -> Mock:
    """Create a mock DragonpawBot for check_channel_perms tests."""
    bot = Mock()
    bot.user_id = hikari.Snowflake(1)

    # Mock member with no extra roles
    member = Mock(spec=hikari.Member)
    member.id = hikari.Snowflake(1)
    member.role_ids = []
    bot.rest.fetch_member = AsyncMock(return_value=member)

    # Mock @everyone role
    everyone_role = Mock(spec=hikari.Role)
    everyone_role.id = hikari.Snowflake(50)  # guild_id
    everyone_role.permissions = role_perms
    bot.rest.fetch_roles = AsyncMock(return_value=[everyone_role])

    if fetch_channel_side_effect:
        bot.rest.fetch_channel = AsyncMock(side_effect=fetch_channel_side_effect)
    else:
        channel = Mock(spec=hikari.GuildTextChannel)
        channel.permission_overwrites = {}
        bot.rest.fetch_channel = AsyncMock(return_value=channel)

    return bot


GUILD_ID = hikari.Snowflake(50)
CHANNEL_ID = hikari.Snowflake(100)


async def test_check_channel_perms_forbidden_returns_view_channel():
    bot = _mock_bot(
        fetch_channel_side_effect=hikari.ForbiddenError(
            url="test", headers={}, raw_body=b""
        ),
    )
    result = await check_channel_perms(bot, GUILD_ID, CHANNEL_ID)
    assert result == ["View Channel (cannot access channel)"]


async def test_check_channel_perms_not_found_returns_channel_not_found():
    bot = _mock_bot(
        fetch_channel_side_effect=hikari.NotFoundError(
            url="test", headers={}, raw_body=b""
        ),
    )
    result = await check_channel_perms(bot, GUILD_ID, CHANNEL_ID)
    assert result == ["Channel not found (may have been deleted)"]


async def test_check_channel_perms_admin_bypass():
    bot = _mock_bot(role_perms=hikari.Permissions.ADMINISTRATOR)
    result = await check_channel_perms(bot, GUILD_ID, CHANNEL_ID)
    assert result == []
    # Should not even try to fetch the channel
    bot.rest.fetch_channel.assert_not_called()


async def test_check_channel_perms_missing_permissions():
    bot = _mock_bot(role_perms=hikari.Permissions.NONE)
    result = await check_channel_perms(bot, GUILD_ID, CHANNEL_ID)
    assert "Send Messages" in result
    assert "Embed Links" in result
    assert "Attach Files" in result


async def test_check_channel_perms_all_present():
    bot = _mock_bot(
        role_perms=(
            hikari.Permissions.SEND_MESSAGES
            | hikari.Permissions.EMBED_LINKS
            | hikari.Permissions.ATTACH_FILES
        ),
    )
    result = await check_channel_perms(bot, GUILD_ID, CHANNEL_ID)
    assert result == []
