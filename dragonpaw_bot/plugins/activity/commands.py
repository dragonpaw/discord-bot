"""Slash commands: /activity score, /activity report"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.colors import SOLARIZED_CYAN
from dragonpaw_bot.context import NotAuthorized
from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import (
    ACTIVITY_FLOOR,
    ActivityGuildState,
    best_role_config,
    calculate_score,
    has_ignored_role,
)

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)
loader = lightbulb.Loader()

_EMBED_DESCRIPTION_LIMIT = 4096
_TRUNCATION_NOTE = "\n*(list truncated — too many members to display)*"

_activity_group = lightbulb.Group("activity", "Activity tracker commands")


@lightbulb.hook(
    lightbulb.ExecutionSteps.CHECKS, skip_when_failed=True, name="activity_viewer_only"
)
def activity_viewer_only(
    _: lightbulb.ExecutionPipeline, ctx: lightbulb.Context
) -> None:
    """Hook: allows guild admins and members with the configured activity viewer role."""
    if ctx.guild_id is None or ctx.member is None:
        raise NotAuthorized()
    if ctx.member.permissions & (
        hikari.Permissions.ADMINISTRATOR | hikari.Permissions.MANAGE_GUILD
    ):
        return
    st = activity_state.load(int(ctx.guild_id))
    if st.config.viewer_role_id is None:
        raise NotAuthorized()
    if st.config.viewer_role_id not in {int(r) for r in ctx.member.role_ids}:
        raise NotAuthorized


def _classify_members(
    member_map: dict[int, hikari.Member],
    st: ActivityGuildState,
    now: float,
) -> tuple[list[tuple[hikari.Member, str]], list[tuple[float, hikari.Member]]]:
    """Split non-bot members into immune (with role name) and scored lists."""
    immune: list[tuple[hikari.Member, str]] = []
    scored: list[tuple[float, hikari.Member]] = []
    for member in member_map.values():
        if member.is_bot:
            continue
        role_ids = [int(r) for r in member.role_ids]
        immune_role = has_ignored_role(role_ids, st.config.role_configs)
        if immune_role is not None:
            immune.append((member, immune_role))
            continue
        user_activity = st.users.get(int(member.id))
        if user_activity is not None:
            buckets = user_activity.buckets
        else:
            buckets = []
        scored.append(
            (
                calculate_score(
                    buckets, best_role_config(role_ids, st.config.role_configs), now=now
                ),
                member,
            )
        )
    return immune, scored


def _build_report_lines(
    immune_members: list[tuple[hikari.Member, str]],
    scored_members: list[tuple[float, hikari.Member]],
) -> list[str]:
    """Build display lines sorted alphabetically by display name."""
    medals = ["🥇", "🥈", "🥉"]
    medal_map: dict[int, str] = {}
    for idx, (_, member) in enumerate(
        sorted(scored_members, key=lambda x: x[0], reverse=True)[:3]
    ):
        medal_map[int(member.id)] = medals[idx]

    score_by_id: dict[int, float] = {
        int(member.id): score for score, member in scored_members
    }
    immune_role_by_id: dict[int, str] = {
        int(member.id): role_name for member, role_name in immune_members
    }
    all_members: list[hikari.Member] = [m for m, _ in immune_members] + [
        m for _, m in scored_members
    ]
    all_members.sort(key=lambda m: m.display_name.lower())

    lines: list[str] = []
    for member in all_members:
        member_id = int(member.id)
        if member_id not in score_by_id:
            lines.append(
                f"🛡️ {member.mention} — Immune ({immune_role_by_id[member_id]})"
            )
            continue
        score = score_by_id[member_id]
        medal = medal_map.get(member_id)
        if medal is not None:
            lines.append(f"{medal} {member.mention} — {score:.2f}")
        elif score >= ACTIVITY_FLOOR:
            lines.append(f"🐉 {member.mention} — {score:.2f}")
        else:
            lines.append(f"💤 {member.mention} — {score:.2f}")
    return lines


def _truncate_description(lines: list[str]) -> str:
    """Join lines into an embed description, truncating if needed."""
    description = "\n".join(lines)
    if len(description) <= _EMBED_DESCRIPTION_LIMIT:
        return description
    kept: list[str] = []
    budget = _EMBED_DESCRIPTION_LIMIT - len(_TRUNCATION_NOTE)
    for line in lines:
        if kept:
            needed = len(line) + 1  # +1 for the '\n' separator
        else:
            needed = len(line)
        if budget - needed < 0:
            break
        kept.append(line)
        budget -= needed
    return "\n".join(kept) + _TRUNCATION_NOTE


class ActivityScore(
    lightbulb.SlashCommand,
    name="score",
    description="Show activity score for a member",
    hooks=[activity_viewer_only],
):
    user = lightbulb.user("user", "Member to check (defaults to you)", default=None)

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]

        response_id = await ctx.respond(
            "Sniffing the hoard... 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        target_user = self.user or ctx.user
        member = bot.cache.get_member(ctx.guild_id, target_user.id)
        if member is None:
            try:
                member = await bot.rest.fetch_member(ctx.guild_id, target_user.id)
            except hikari.NotFoundError:
                await ctx.edit_response(
                    response_id,
                    content=f"🐉 Couldn't find {target_user.mention} in this server!",
                )
                return

        st = activity_state.load(int(ctx.guild_id))
        role_ids = [int(r) for r in member.role_ids]
        role_cfg = best_role_config(role_ids, st.config.role_configs)

        user_activity = st.users.get(int(member.id))
        buckets = user_activity.buckets if user_activity else []
        score = calculate_score(buckets, role_cfg, now=time.time())

        immune_role = has_ignored_role(role_ids, st.config.role_configs)
        if immune_role is not None:
            status_line = f"🛡️ Immune ({immune_role})"
        elif score >= ACTIVITY_FLOOR:
            status_line = "🐉 Active"
        else:
            status_line = "💤 Lurking"

        role_note = f" (role: **{role_cfg.role_name}**)" if role_cfg else ""

        await ctx.edit_response(
            response_id,
            content="",
            embed=hikari.Embed(
                title=f"📊 Activity Score — {member.display_name}",
                description=(
                    f"**Score:** {score:.2f}\n"
                    f"**Status:** {status_line}\n"
                    f"**Contributions:** {len(buckets)} hourly bucket(s){role_note}"
                ),
                color=SOLARIZED_CYAN,
            ),
        )
        logger.info(
            "Activity score checked",
            guild=st.guild_name,
            target=member.display_name,
            score=score,
        )


class ActivityReport(
    lightbulb.SlashCommand,
    name="report",
    description="Show all members with their activity status",
    hooks=[activity_viewer_only],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]

        response_id = await ctx.respond(
            "Tallying the hoard... 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        st = activity_state.load(int(ctx.guild_id))

        member_map: dict[int, hikari.Member] = {}
        try:
            async for member in bot.rest.fetch_members(ctx.guild_id):
                member_map[int(member.id)] = member
        except hikari.HTTPError:
            logger.warning("Failed to fetch members for report", guild=st.guild_name)
            await ctx.edit_response(
                response_id,
                content="🐉 Couldn't fetch member list right now — try again in a moment! 🐾",
            )
            return

        immune_members, scored_members = _classify_members(member_map, st, time.time())

        if not immune_members and not scored_members:
            await ctx.edit_response(
                response_id,
                content="🐉 No eligible members found~ 🐾",
            )
            return

        lines = _build_report_lines(immune_members, scored_members)

        await ctx.edit_response(
            response_id,
            content="",
            embed=hikari.Embed(
                title="📋 Activity Report",
                description=_truncate_description(lines),
                color=SOLARIZED_CYAN,
            ),
        )
        logger.info(
            "Activity report viewed",
            guild=st.guild_name,
            member_count=len(lines),
        )


_activity_group.register(ActivityScore)
_activity_group.register(ActivityReport)
loader.command(_activity_group)
