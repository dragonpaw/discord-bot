from __future__ import annotations

from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot import journal

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

loader = lightbulb.Loader()


def display_name_change(
    old: hikari.Member | None, new: hikari.Member | None
) -> tuple[str, str] | None:
    """The (before, after) pair, or None when the display name didn't move.

    MemberUpdateEvent also fires on avatar and role changes, so without this
    filter every role assignment would land in the journal.
    """
    if old is None or new is None:
        return None
    if old.display_name == new.display_name:
        return None
    return old.display_name, new.display_name


@loader.listener(hikari.MemberUpdateEvent)
async def on_member_update(event: hikari.MemberUpdateEvent) -> None:
    """Journal display name changes.

    These never reach the log channel — a few hundred renames a year would
    drown it — so they are written straight to the store.
    """
    change = display_name_change(event.old_member, event.member)
    if change is None:
        return
    before, after = change

    # These bypass gc.log(), so the log channel can't be their opt-in signal.
    # A guild with no staff role has nobody who could ever read them.
    st = journal.load(int(event.guild_id))
    if st.staff_role_id is None:
        return

    bot: DragonpawBot = event.app  # type: ignore[assignment]
    guild = bot.cache.get_guild(event.guild_id)
    journal.record(
        int(event.guild_id),
        guild.name if guild else str(event.guild_id),
        user_id=int(event.member.id),
        user_name=after,
        kind="name_change",
        summary=(
            f"Changed name from **{journal.escape_markdown(before)}** "
            f"to **{journal.escape_markdown(after)}**"
        ),
    )
