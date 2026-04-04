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
from dragonpaw_bot.plugins.activity.chart import render_activity_chart
from dragonpaw_bot.plugins.activity.models import (
    ACTIVITY_FLOOR,
    ActivityGuildMeta,
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
    meta = activity_state.load_config(int(ctx.guild_id))
    if meta.config.viewer_role_id is None:
        raise NotAuthorized()
    if meta.config.viewer_role_id not in {int(r) for r in ctx.member.role_ids}:
        raise NotAuthorized()


def _classify_members(
    member_map: dict[int, hikari.Member],
    meta: ActivityGuildMeta,
    now: float,
    owner_id: int | None = None,
) -> tuple[list[tuple[hikari.Member, str, float]], list[tuple[float, hikari.Member]]]:
    """Split non-bot members into immune (with role name and score) and scored lists."""
    immune: list[tuple[hikari.Member, str, float]] = []
    scored: list[tuple[float, hikari.Member]] = []
    for member in member_map.values():
        if member.is_bot:
            continue
        role_ids = [int(r) for r in member.role_ids]
        immune_role = has_ignored_role(role_ids, meta.config.role_configs)
        if immune_role is None and owner_id is not None and int(member.id) == owner_id:
            immune_role = "Guild Owner"
        ua = activity_state.load_user(meta.guild_id, int(member.id))
        buckets = ua.buckets if ua is not None else []
        score = calculate_score(
            buckets,
            best_role_config(role_ids, meta.config.role_configs),
            now=now,
        )
        if immune_role is not None:
            immune.append((member, immune_role, score))
        else:
            scored.append((score, member))
    return immune, scored


def _build_report_lines(
    immune_members: list[tuple[hikari.Member, str, float]],
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
    immune_by_id: dict[int, tuple[str, float]] = {
        int(member.id): (role_name, score)
        for member, role_name, score in immune_members
    }
    all_members: list[hikari.Member] = [m for m, _, _ in immune_members] + [
        m for _, m in scored_members
    ]
    all_members.sort(key=lambda m: m.display_name.lower())

    lines: list[str] = []
    for member in all_members:
        member_id = int(member.id)
        if member_id not in score_by_id:
            role_name, score = immune_by_id[member_id]
            lines.append(f"🛡️ {member.mention} — Immune ({role_name}) — {score:.2f}")
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


def _can_view_others(ctx: lightbulb.Context) -> bool:
    """Return True if the invoker has admin/manage-guild or the configured viewer role."""
    assert ctx.member and ctx.guild_id
    if ctx.member.permissions & (
        hikari.Permissions.ADMINISTRATOR | hikari.Permissions.MANAGE_GUILD
    ):
        return True
    meta = activity_state.load_config(int(ctx.guild_id))
    viewer_id = meta.config.viewer_role_id
    return viewer_id is not None and viewer_id in {int(r) for r in ctx.member.role_ids}


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
):
    user = lightbulb.user("user", "Member to check (defaults to you)", default=None)

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        assert ctx.member
        bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]

        response_id = await ctx.respond(
            "Sniffing the hoard... 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        target_user = self.user or ctx.user

        # Anyone can check their own score; viewing others requires viewer permission.
        if int(target_user.id) != int(ctx.user.id) and not _can_view_others(ctx):
            await ctx.edit_response(
                response_id,
                content="*snorts smoke* 🐉 You need the viewer role to check someone else's score! 🐾",
            )
            return

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

        meta = activity_state.load_config(int(ctx.guild_id))
        role_ids = [int(r) for r in member.role_ids]
        role_cfg = best_role_config(role_ids, meta.config.role_configs)

        ua = activity_state.load_user(meta.guild_id, int(member.id))
        buckets = ua.buckets if ua is not None else []
        score = calculate_score(buckets, role_cfg, now=time.time())

        guild = bot.cache.get_guild(ctx.guild_id) or await bot.rest.fetch_guild(
            ctx.guild_id
        )
        owner_id = int(guild.owner_id)

        immune_role = has_ignored_role(role_ids, meta.config.role_configs)
        if immune_role is None and int(member.id) == owner_id:
            immune_role = "Guild Owner"
        if immune_role is not None:
            status_emoji = "🛡️"
            status_line = f"🛡️ Immune ({immune_role})"
        elif score >= ACTIVITY_FLOOR:
            status_emoji = "🐉"
            status_line = "🐉 Active"
        else:
            status_emoji = "💤"
            status_line = "💤 Lurking"

        role_note = f" (role: **{role_cfg.role_name}**)" if role_cfg else ""

        chart = render_activity_chart(member.display_name, buckets, score, status_emoji)
        embed = hikari.Embed(
            title=f"📊 Activity Score — {member.display_name}",
            description=(
                f"**Score:** {score:.2f}\n"
                f"**Status:** {status_line}\n"
                f"**Contributions:** {len(buckets)} hourly bucket(s){role_note}"
            ),
            color=SOLARIZED_CYAN,
        )
        embed.set_image("attachment://activity_chart.png")
        await ctx.edit_response(
            response_id,
            content="",
            embed=embed,
            attachments=[chart],
        )
        logger.info(
            "Activity score checked",
            guild=meta.guild_name,
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
        assert ctx.member
        bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]

        response_id = await ctx.respond(
            "Tallying the hoard... 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        meta = activity_state.load_config(int(ctx.guild_id))

        member_map: dict[int, hikari.Member] = {}
        try:
            async for member in bot.rest.fetch_members(ctx.guild_id):
                member_map[int(member.id)] = member
        except hikari.HTTPError:
            logger.warning("Failed to fetch members for report", guild=meta.guild_name)
            await ctx.edit_response(
                response_id,
                content="🐉 Couldn't fetch member list right now — try again in a moment! 🐾",
            )
            return

        guild = bot.cache.get_guild(ctx.guild_id) or await bot.rest.fetch_guild(
            ctx.guild_id
        )
        owner_id = int(guild.owner_id)
        immune_members, scored_members = _classify_members(
            member_map, meta, time.time(), owner_id
        )

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
            guild=meta.guild_name,
            user=ctx.member.display_name,
            member_count=len(lines),
        )


_activity_group.register(ActivityScore)
_activity_group.register(ActivityReport)
loader.command(_activity_group)
