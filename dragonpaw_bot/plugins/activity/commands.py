"""Slash commands: /activity score, /activity leaderboard"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot.colors import SOLARIZED_CYAN
from dragonpaw_bot.plugins.activity import state as activity_state
from dragonpaw_bot.plugins.activity.models import (
    ACTIVITY_FLOOR,
    best_role_config,
    calculate_score,
    has_ignored_role,
)

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)

_DEFAULT_LEADERBOARD_COUNT = 10
_MAX_LEADERBOARD_COUNT = 25


def register(activity_group: lightbulb.Group) -> None:
    activity_group.register(ActivityScore)
    activity_group.register(ActivityLeaderboard)


class ActivityScore(
    lightbulb.SlashCommand,
    name="score",
    description="Show activity score for a member",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_GUILD)],
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
        now = time.time()
        score = calculate_score(buckets, role_cfg, now=now)
        active = score >= ACTIVITY_FLOOR

        status_line = "🐉 Active" if active else "💤 Lurking"
        bucket_count = len(buckets)
        role_note = f" (role: **{role_cfg.role_name}**)" if role_cfg else ""

        embed = hikari.Embed(
            title=f"📊 Activity Score — {member.display_name}",
            description=(
                f"**Score:** {score:.2f}\n"
                f"**Status:** {status_line}\n"
                f"**Contributions:** {bucket_count} hourly bucket(s){role_note}"
            ),
            color=SOLARIZED_CYAN,
        )
        await ctx.edit_response(response_id, embed=embed)
        logger.info(
            "Activity score checked",
            guild=st.guild_name,
            target=member.display_name,
            score=score,
        )


class ActivityLeaderboard(
    lightbulb.SlashCommand,
    name="leaderboard",
    description="Show top members by activity score",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_GUILD)],
):
    count = lightbulb.integer(
        "count",
        f"Number of members to show (default {_DEFAULT_LEADERBOARD_COUNT}, max {_MAX_LEADERBOARD_COUNT})",
        default=_DEFAULT_LEADERBOARD_COUNT,
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]

        response_id = await ctx.respond(
            "Tallying the hoard... 🐉",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        n = max(1, min(self.count, _MAX_LEADERBOARD_COUNT))
        st = activity_state.load(int(ctx.guild_id))

        if not st.users:
            await ctx.edit_response(
                response_id,
                content="🐉 No activity data yet! Start chatting to get on the board~ 🐾",
            )
            return

        # Build member map from cache
        member_map: dict[int, hikari.Member] = {}
        async for member in bot.rest.fetch_members(ctx.guild_id):
            member_map[int(member.id)] = member

        now = time.time()
        scored: list[tuple[float, hikari.Member]] = []

        for user_id, user_activity in st.users.items():
            member = member_map.get(user_id)
            if member is None or member.is_bot:
                continue
            role_ids = [int(r) for r in member.role_ids]
            if has_ignored_role(role_ids, st.config.role_configs):
                continue
            role_cfg = best_role_config(role_ids, st.config.role_configs)
            score = calculate_score(user_activity.buckets, role_cfg, now=now)
            scored.append((score, member))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:n]

        if not top:
            await ctx.edit_response(
                response_id,
                content="🐉 No eligible members with activity data yet~ 🐾",
            )
            return

        lines: list[str] = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, (score, member) in enumerate(top):
            prefix = medals[idx] if idx < len(medals) else f"**{idx + 1}.**"
            active_mark = "" if score >= ACTIVITY_FLOOR else " 💤"
            lines.append(f"{prefix} {member.mention} — {score:.2f}{active_mark}")

        embed = hikari.Embed(
            title="🏆 Activity Leaderboard",
            description="\n".join(lines),
            color=SOLARIZED_CYAN,
        )
        await ctx.edit_response(response_id, embed=embed)
        logger.info(
            "Activity leaderboard viewed",
            guild=st.guild_name,
            count=len(top),
        )
