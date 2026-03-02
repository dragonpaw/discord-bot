# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import hikari
import lightbulb

from dragonpaw_bot import utils
from dragonpaw_bot.colors import (
    SOLARIZED_MAGENTA,
    SOLARIZED_VIOLET,
    SOLARIZED_YELLOW,
)
from dragonpaw_bot.plugins.subday import chart, prompts, state
from dragonpaw_bot.plugins.subday.models import SubDayGuildConfig, SubDayParticipant

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = logging.getLogger(__name__)

MILESTONE_ROLES: dict[int, str] = {
    13: "SubChallenge: 13wks",
    26: "SubChallenge: 26wks",
    39: "SubChallenge: 39wks",
    52: "SubChallenge: 52wks",
}
TOTAL_WEEKS = 52


# ---------------------------------------------------------------------------- #
#                                   Helpers                                    #
# ---------------------------------------------------------------------------- #


async def help_handler(ctx: lightbulb.Context) -> None:
    """Show contextual help listing commands the user can access."""
    assert ctx.guild_id
    guild_state = state.load(int(ctx.guild_id))
    cfg = guild_state.config

    lines = [
        "`/subday about` — Learn about the program",
        "`/subday status` — Check your progress",
    ]
    if ctx.member and _has_permission(ctx, ctx.member, cfg.enroll_role):
        lines.append("`/subday signup` — Sign up for the program")
    if ctx.member and _has_permission(ctx, ctx.member, cfg.complete_role):
        lines.append("`/subday complete @user` — Mark a week complete")
        lines.append("`/subday list` — View all participants")
        lines.append("`/subday remove @user` — Remove a participant")
    if ctx.member and _has_permission(ctx, ctx.member, cfg.backfill_role):
        lines.append("`/subday setweek @user <week>` — Set week")
    if ctx.app.owner_ids and ctx.author.id in ctx.app.owner_ids:
        lines.append("`/subday config` — Configure settings (owner)")
        lines.append("`/subday prizes` — Set milestone prizes (owner)")

    embed = hikari.Embed(
        title="Where I am Led — Commands",
        description="\n".join(lines),
        color=SOLARIZED_VIOLET,
    )
    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


def _has_permission(
    ctx: lightbulb.Context, member: hikari.Member, role_name: str | None
) -> bool:
    """Check if member has the named role, or is bot owner if role is None."""
    if role_name:
        return utils.member_has_role(member, role_name)
    return bool(ctx.app.owner_ids and ctx.author.id in ctx.app.owner_ids)


# ---------------------------------------------------------------------------- #
#                              Achievement embeds                              #
# ---------------------------------------------------------------------------- #


def _regular_completion_embed(member: hikari.Member, week: int) -> hikari.Embed:
    return hikari.Embed(
        title="⭐ Where I am Led ⭐",
        description=(
            f"⭐ {member.mention} has completed **Week {week}** of "
            f"Where I am Led! ({week}/{TOTAL_WEEKS}) ⭐"
        ),
        color=SOLARIZED_VIOLET,
    )


def _milestone_embed(
    member: hikari.Member, week: int, role_name: str, prizes: dict[int, str]
) -> hikari.Embed:
    prize = prizes.get(week, "a prize")
    return hikari.Embed(
        title="🌟✨ Where I am Led — Milestone! ✨🌟",
        description=(
            f"🌟 {member.mention} has reached the **Week {week}** milestone "
            f"of Where I am Led! 🌟\n\n"
            f"⭐ They have earned the **{role_name}** role! ⭐\n\n"
            f"🎁 Their prize: **{prize}**! 🎁\n\n"
            f"✨ ({week}/{TOTAL_WEEKS}) ✨"
        ),
        color=SOLARIZED_YELLOW,
    )


def _graduation_embed(member: hikari.Member, prizes: dict[int, str]) -> hikari.Embed:
    embed = hikari.Embed(
        title="🌟💫✨ Where I am Led — GRADUATION ✨💫🌟",
        description=(
            f"# 🎓 {member.mention} has graduated! 🎓\n\n"
            f"⭐ After **{TOTAL_WEEKS} weeks** of dedication, introspection, and growth, "
            f"{member.mention} has completed the entire **Where I am Led** journey. ⭐\n\n"
            f"🌟 This is a testament to their commitment, courage, and the beautiful "
            f"spirit of service they carry within them. They have explored who they are, "
            f"what they value, and what it means to walk this path with grace. 🌟\n\n"
            f"🎁 Their prize: **{prizes[TOTAL_WEEKS]}**! 🎁\n\n"
            f"💫 Please join us in celebrating this remarkable achievement! 💫\n\n"
            f"✨⭐🌟💫🌠🌟⭐✨"
        ),
        color=SOLARIZED_MAGENTA,
    )
    embed.set_footer(text="✨ Where I am Led — 52 weeks of growth and discovery ✨")
    return embed


async def _dm_completion(
    target: hikari.Member,
    week: int,
    chart_bytes: hikari.Bytes,
) -> None:
    """DM the target their completion embed and star chart."""
    try:
        dm = await target.user.fetch_dm_channel()
        embed = _regular_completion_embed(target, week)
        embed.set_image("attachment://star_chart.png")
        await dm.send(embed=embed, attachment=chart_bytes)
        logger.info("U=%r: Sent completion DM for week %d", target.username, week)
    except hikari.ForbiddenError:
        logger.warning("U=%r: Cannot DM user for completion notice", target.username)


async def _post_achievement(  # noqa: PLR0913
    bot: DragonpawBot,
    guild_id: hikari.Snowflake,
    completer: hikari.Member,
    target: hikari.Member,
    week: int,
    cfg: SubDayGuildConfig,
) -> None:
    """Post achievement embed and handle milestone/graduation rewards."""
    logger.debug("U=%r: Posting achievement for week %d", target.username, week)
    prizes = cfg.milestone_prizes()

    # Fetch channels once upfront
    channels = await bot.rest.fetch_guild_channels(guild_id)
    channel_map = {
        c.name: c for c in channels if isinstance(c, hikari.GuildTextChannel)
    }

    achievements = None
    if cfg.achievements_channel:
        achievements = channel_map.get(cfg.achievements_channel)
        if not achievements:
            logger.warning(
                "No #%s channel found, achievement embed will not be posted",
                cfg.achievements_channel,
            )

    # Generate star chart attachment
    chart_bytes = chart.render_star_chart(
        username=target.username,
        current_week=week,
        week_completed=True,
    )

    await _dm_completion(target, week, chart_bytes)

    # Regular completion — just post the embed
    if week not in MILESTONE_ROLES:
        if achievements:
            embed = _regular_completion_embed(target, week)
            embed.set_image("attachment://star_chart.png")
            await achievements.send(embed=embed, attachment=chart_bytes)
        return

    # Milestone or graduation
    if week == TOTAL_WEEKS:
        logger.info("U=%r: GRADUATED from Where I am Led!", target.username)
        embed = _graduation_embed(target, prizes)
        staff_msg = (
            f"{completer.mention} — {target.mention} has **graduated** "
            f"from Where I am Led! Please arrange their prize: "
            f"**{prizes[TOTAL_WEEKS]}**."
        )
    else:
        role_name = MILESTONE_ROLES[week]
        logger.info(
            "U=%r: Reached milestone week %d (%s)", target.username, week, role_name
        )
        embed = _milestone_embed(target, week, role_name, prizes)
        prize = prizes.get(week, "a prize")
        staff_msg = (
            f"{completer.mention} — {target.mention} has reached the "
            f"**week {week} milestone** of Where I am Led! "
            f"Please arrange their prize: **{prize}**."
        )

    if achievements:
        embed.set_image("attachment://star_chart.png")
        await achievements.send(embed=embed, attachment=chart_bytes)

    # Assign milestone role
    role = await utils.guild_role_by_name(bot, guild_id, MILESTONE_ROLES[week])
    if role:
        await target.add_role(role, reason=f"SubDay: week {week}")
        logger.info("U=%r: Assigned role %r", target.username, role.name)
    else:
        logger.warning("Role %r not found in guild", MILESTONE_ROLES[week])

    # Notify staff
    if cfg.staff_channel:
        staff = channel_map.get(cfg.staff_channel)
        if staff:
            await staff.send(staff_msg)
        else:
            logger.warning("No #%s channel found for staff notice", cfg.staff_channel)


# ---------------------------------------------------------------------------- #
#                                   Commands                                   #
# ---------------------------------------------------------------------------- #


def register(subday_group: lightbulb.SlashCommandGroup) -> None:
    """Register all subcommands on the given command group."""
    _register_about(subday_group)
    _register_status(subday_group)
    _register_signup(subday_group)
    _register_complete(subday_group)
    _register_list(subday_group)
    _register_remove(subday_group)
    _register_setweek(subday_group)
    _register_config(subday_group)
    _register_prizes(subday_group)


def _register_about(subday_group: lightbulb.SlashCommandGroup) -> None:
    weeks_dir = Path(__file__).parent / "weeks"

    @subday_group.child
    @lightbulb.command("about", "Learn about the Where I am Led program")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_about(ctx: lightbulb.Context) -> None:
        logger.info(
            "U=%r: Viewed SubDay about",
            ctx.author.username,
        )
        text = (weeks_dir / "about.md").read_text()

        title = "Where I am Led"
        for line in text.splitlines():
            if line.startswith("# ") and not line.startswith("## "):
                title = line[2:].strip()
                break

        sections = prompts._split_sections(text)
        embed = hikari.Embed(title=title, color=SOLARIZED_VIOLET)
        for heading, body in sections.items():
            embed.add_field(name=heading, value=body or "\u200b", inline=False)
        await ctx.respond(embed=embed)


def _register_status(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.command("status", "Check your own progress in Where I am Led")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_status(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        logger.info("U=%r: Checking SubDay status", ctx.author.username)

        guild_state = state.load(int(ctx.guild_id))
        user_id = int(ctx.author.id)

        if user_id not in guild_state.participants:
            await ctx.respond(
                "You're not signed up for Where I am Led. "
                "Use `/subday signup` to get started!",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        p = guild_state.participants[user_id]

        if p.current_week > TOTAL_WEEKS:
            chart_bytes = chart.render_star_chart(
                username=ctx.author.username,
                current_week=p.current_week,
                week_completed=p.week_completed,
            )
            embed = hikari.Embed(
                title="Where I am Led — Graduated!",
                description=(
                    "You've **graduated** from Where I am Led! "
                    "Congratulations on completing all 52 weeks!"
                ),
                color=SOLARIZED_MAGENTA,
            )
            embed.set_image("attachment://star_chart.png")
            await ctx.respond(
                embed=embed,
                attachment=chart_bytes,
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        if p.week_completed:
            status_text = (
                f"You've completed **Week {p.current_week}**. "
                "Your next prompt will arrive on Sunday!"
            )
        else:
            status_text = (
                f"You're currently on **Week {p.current_week}**. "
                "Complete it and show a reviewer to move on."
            )

        # Show next milestone
        next_milestone = None
        for m in sorted(MILESTONE_ROLES):
            if m >= p.current_week:
                next_milestone = m
                break

        if next_milestone:
            weeks_away = next_milestone - p.current_week
            if weeks_away == 0:
                milestone_text = (
                    f"This is a milestone week! ({MILESTONE_ROLES[next_milestone]})"
                )
            else:
                milestone_text = (
                    f"Next milestone: **Week {next_milestone}** "
                    f"({MILESTONE_ROLES[next_milestone]}) — "
                    f"{weeks_away} week{'s' if weeks_away != 1 else ''} away"
                )
            status_text += f"\n\n{milestone_text}"

        chart_bytes = chart.render_star_chart(
            username=ctx.author.username,
            current_week=p.current_week,
            week_completed=p.week_completed,
        )

        embed = hikari.Embed(
            title="Where I am Led — Your Progress",
            description=status_text,
            color=SOLARIZED_VIOLET,
        )
        embed.set_image("attachment://star_chart.png")
        embed.add_field(
            name="Progress",
            value=f"{p.current_week}/{TOTAL_WEEKS} weeks",
            inline=True,
        )
        embed.add_field(
            name="Signed up",
            value=f"<t:{int(p.signup_date.timestamp())}:R>",
            inline=True,
        )

        await ctx.respond(
            embed=embed,
            attachment=chart_bytes,
            flags=hikari.MessageFlag.EPHEMERAL,
        )


def _register_signup(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.command("signup", "Sign up for the Where I am Led program")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_signup(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member
        bot: DragonpawBot = ctx.app  # type: ignore[assignment]

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not _has_permission(ctx, ctx.member, cfg.enroll_role):
            label = (
                f"**{cfg.enroll_role}** role" if cfg.enroll_role else "bot owner status"
            )
            logger.debug(
                "U=%r: SubDay signup denied, missing %s",
                ctx.author.username,
                label,
            )
            await ctx.respond(
                f"You need {label} to sign up.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        user_id = int(ctx.author.id)

        if user_id in guild_state.participants:
            logger.debug(
                "G=%r U=%r: SubDay signup rejected, already enrolled",
                guild_state.guild_name,
                ctx.author.username,
            )
            await ctx.respond(
                "You are already signed up! Use `/subday status` to check "
                "your progress.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        participant = SubDayParticipant(
            user_id=user_id,
            signup_date=datetime.datetime.now(tz=datetime.UTC),
        )
        guild_state.participants[user_id] = participant

        guild = await bot.rest.fetch_guild(ctx.guild_id)
        guild_state.guild_name = guild.name
        state.save(guild_state)

        # DM week 1
        rules_text = prompts.load_rules()
        prompt = prompts.load_week(1)
        embed = prompts.build_prompt_embed(prompt)

        try:
            dm = await ctx.author.fetch_dm_channel()
            await dm.send(
                content=(
                    "**Welcome to Where I am Led!**\n\n"
                    "This is a 52-week guided journal program. Each week you'll "
                    "receive a prompt here via DM.\n\n"
                    f"**Instructions:**\n{rules_text}\n\n"
                    "Here is your first week's prompt:"
                ),
            )
            await dm.send(embed=embed)
            participant.week_sent = True
            state.save(guild_state)
            await ctx.respond(
                "You've been signed up for **Where I am Led**! "
                "Check your DMs for your first prompt.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
        except hikari.ForbiddenError:
            logger.warning(
                "G=%r U=%r: Cannot DM user for subday signup",
                guild.name,
                ctx.author.username,
            )
            await ctx.respond(
                "You've been signed up for **Where I am Led**! "
                "However, I couldn't DM you your first prompt. "
                "Please enable DMs from server members and ask "
                "staff to have your prompt re-sent.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
        logger.info(
            "G=%r U=%r: Signed up for SubDay",
            guild.name,
            ctx.author.username,
        )


def _register_complete(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.option("user", "The participant to mark complete", type=hikari.Member)
    @lightbulb.command("complete", "Mark a participant's current week as complete")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_complete(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member
        bot: DragonpawBot = ctx.app  # type: ignore[assignment]

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not _has_permission(ctx, ctx.member, cfg.complete_role):
            label = (
                f"**{cfg.complete_role}** role"
                if cfg.complete_role
                else "bot owner status"
            )
            logger.debug(
                "U=%r: SubDay complete denied, missing %s",
                ctx.author.username,
                label,
            )
            await ctx.respond(
                f"You need {label} to use this command.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        target: hikari.Member = ctx.options.user
        target_id = int(target.id)

        # Prevent self-completion
        if target.id == ctx.author.id:
            logger.debug("U=%r: SubDay self-completion blocked", ctx.author.username)
            await ctx.respond(
                "You cannot mark your own work as complete.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        if target_id not in guild_state.participants:
            await ctx.respond(
                f"{target.mention} is not signed up for Where I am Led.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        participant = guild_state.participants[target_id]

        if participant.week_completed:
            await ctx.respond(
                f"{target.mention} has already completed week "
                f"{participant.current_week}. "
                "They'll receive their next prompt on Sunday.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        if participant.current_week > TOTAL_WEEKS:
            await ctx.respond(
                f"{target.mention} has already graduated!",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        week = participant.current_week
        participant.week_completed = True
        participant.last_completed_date = datetime.datetime.now(tz=datetime.UTC)
        state.save(guild_state)

        await _post_achievement(bot, ctx.guild_id, ctx.member, target, week, cfg)

        await ctx.respond(
            f"Marked {target.mention} as complete for **Week {week}**.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        logger.info(
            "G=%r U=%r: Completed SubDay week %d (marked by %s)",
            guild_state.guild_name,
            target.username,
            week,
            ctx.author.username,
        )


def _register_list(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.command("list", "List all participants and their progress")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_list(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not _has_permission(ctx, ctx.member, cfg.complete_role):
            label = (
                f"**{cfg.complete_role}** role"
                if cfg.complete_role
                else "bot owner status"
            )
            logger.debug(
                "U=%r: SubDay list denied, missing %s",
                ctx.author.username,
                label,
            )
            await ctx.respond(
                f"You need {label} to use this command.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        logger.info(
            "G=%r U=%r: Listing SubDay participants (%d enrolled)",
            guild_state.guild_name,
            ctx.author.username,
            len(guild_state.participants),
        )

        if not guild_state.participants:
            await ctx.respond(
                "No one is currently signed up for Where I am Led.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        lines: list[str] = []
        for uid, p in sorted(
            guild_state.participants.items(), key=lambda x: x[1].current_week
        ):
            icon = "✅" if p.week_completed else "⏳"
            if p.current_week > TOTAL_WEEKS:
                icon = "🎓"
            lines.append(f"{icon} <@{uid}> — Week {p.current_week}/{TOTAL_WEEKS}")

        embed = hikari.Embed(
            title="Where I am Led — Participants",
            description="\n".join(lines),
            color=SOLARIZED_VIOLET,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


def _register_remove(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.option("user", "The participant to remove", type=hikari.Member)
    @lightbulb.command("remove", "Remove a participant from the program")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_remove(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not _has_permission(ctx, ctx.member, cfg.complete_role):
            label = (
                f"**{cfg.complete_role}** role"
                if cfg.complete_role
                else "bot owner status"
            )
            logger.debug(
                "U=%r: SubDay remove denied, missing %s",
                ctx.author.username,
                label,
            )
            await ctx.respond(
                f"You need {label} to use this command.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        target: hikari.Member = ctx.options.user
        target_id = int(target.id)

        if target_id not in guild_state.participants:
            await ctx.respond(
                f"{target.mention} is not signed up for Where I am Led.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        del guild_state.participants[target_id]
        state.save(guild_state)

        await ctx.respond(
            f"Removed {target.mention} from Where I am Led.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        logger.info(
            "G=%r U=%r: Removed from SubDay by %s",
            guild_state.guild_name,
            target.username,
            ctx.author.username,
        )


def _register_setweek(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.option(
        "week",
        "The week number to set (1-52)",
        type=int,
        min_value=1,
        max_value=TOTAL_WEEKS,
    )
    @lightbulb.option("user", "The participant to adjust", type=hikari.Member)
    @lightbulb.command("setweek", "Set a participant's current week")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_setweek(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not _has_permission(ctx, ctx.member, cfg.backfill_role):
            label = (
                f"**{cfg.backfill_role}** role"
                if cfg.backfill_role
                else "bot owner status"
            )
            logger.debug(
                "U=%r: SubDay setweek denied, missing %s",
                ctx.author.username,
                label,
            )
            await ctx.respond(
                f"You need {label} to use this command.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        target: hikari.Member = ctx.options.user
        target_id = int(target.id)
        week: int = ctx.options.week

        enrolled = target_id in guild_state.participants
        if enrolled:
            participant = guild_state.participants[target_id]
        else:
            participant = SubDayParticipant(
                user_id=target_id,
                signup_date=datetime.datetime.now(tz=datetime.UTC),
            )
            guild_state.participants[target_id] = participant

        participant.current_week = week
        participant.week_completed = False
        participant.week_sent = False

        guild = await ctx.app.rest.fetch_guild(ctx.guild_id)
        guild_state.guild_name = guild.name
        state.save(guild_state)

        if enrolled:
            msg = f"Set {target.mention} to **Week {week}** of Where I am Led."
        else:
            msg = (
                f"Enrolled {target.mention} and set them to "
                f"**Week {week}** of Where I am Led."
            )

        await ctx.respond(msg, flags=hikari.MessageFlag.EPHEMERAL)
        logger.info(
            "G=%r U=%r: SubDay week set to %d by %s (enrolled=%s)",
            guild_state.guild_name,
            target.username,
            week,
            ctx.author.username,
            enrolled,
        )


SUBDAY_CONFIG_PREFIX = "subday_cfg:"


def _config_embed(cfg: SubDayGuildConfig) -> hikari.Embed:
    """Build an embed showing current SubDay config settings."""
    embed = hikari.Embed(
        title="Where I am Led — Configuration",
        description="Use the dropdowns below to change settings. Deselect to clear.",
        color=SOLARIZED_VIOLET,
    )
    embed.add_field(
        name="Enroll role",
        value=f"`{cfg.enroll_role}`" if cfg.enroll_role else "_Owner-only_",
        inline=True,
    )
    embed.add_field(
        name="Complete role",
        value=f"`{cfg.complete_role}`" if cfg.complete_role else "_Owner-only_",
        inline=True,
    )
    embed.add_field(
        name="Backfill role",
        value=f"`{cfg.backfill_role}`" if cfg.backfill_role else "_Owner-only_",
        inline=True,
    )
    embed.add_field(
        name="Achievements channel",
        value=f"`#{cfg.achievements_channel}`"
        if cfg.achievements_channel
        else "_Disabled_",
        inline=True,
    )
    embed.add_field(
        name="Staff channel",
        value=f"`#{cfg.staff_channel}`" if cfg.staff_channel else "_Disabled_",
        inline=True,
    )
    prizes = cfg.milestone_prizes()
    prize_lines = [f"**Week {w}:** {p}" for w, p in sorted(prizes.items())]
    embed.add_field(
        name="Milestone prizes",
        value="\n".join(prize_lines),
        inline=False,
    )
    return embed


def _config_components(bot: lightbulb.BotApp) -> list[hikari.api.ComponentBuilder]:
    """Build the action rows for the config message."""
    rows: list[hikari.api.ComponentBuilder] = []

    # Row 1: Enroll role select
    row1 = bot.rest.build_message_action_row()
    row1.add_select_menu(
        hikari.ComponentType.ROLE_SELECT_MENU,
        f"{SUBDAY_CONFIG_PREFIX}enroll_role",
        placeholder="Enroll role (who can sign up)",
        min_values=0,
        max_values=1,
    )
    rows.append(row1)

    # Row 2: Complete role select
    row2 = bot.rest.build_message_action_row()
    row2.add_select_menu(
        hikari.ComponentType.ROLE_SELECT_MENU,
        f"{SUBDAY_CONFIG_PREFIX}complete_role",
        placeholder="Complete role (who can complete/list/remove)",
        min_values=0,
        max_values=1,
    )
    rows.append(row2)

    # Row 3: Backfill role select
    row3 = bot.rest.build_message_action_row()
    row3.add_select_menu(
        hikari.ComponentType.ROLE_SELECT_MENU,
        f"{SUBDAY_CONFIG_PREFIX}backfill_role",
        placeholder="Backfill role (who can use setweek)",
        min_values=0,
        max_values=1,
    )
    rows.append(row3)

    # Row 4: Achievements channel select
    row4 = bot.rest.build_message_action_row()
    row4.add_channel_menu(
        f"{SUBDAY_CONFIG_PREFIX}achievements_channel",
        channel_types=[hikari.ChannelType.GUILD_TEXT],
        placeholder="Achievements channel (public posts)",
        min_values=0,
        max_values=1,
    )
    rows.append(row4)

    # Row 5: Staff channel select
    row5 = bot.rest.build_message_action_row()
    row5.add_channel_menu(
        f"{SUBDAY_CONFIG_PREFIX}staff_channel",
        channel_types=[hikari.ChannelType.GUILD_TEXT],
        placeholder="Staff channel (milestone notifications)",
        min_values=0,
        max_values=1,
    )
    rows.append(row5)

    return rows


def _resolve_select_value(
    interaction: hikari.ComponentInteraction, field: str
) -> str | None:
    """Extract the name from a role or channel select interaction, or None if cleared."""
    if not interaction.values:
        return None
    snowflake = hikari.Snowflake(interaction.values[0])
    resolved = interaction.resolved
    if not resolved:
        return None
    if field in ("enroll_role", "complete_role", "backfill_role"):
        role = resolved.roles.get(snowflake) if resolved.roles else None
        return role.name if role else None
    channel = resolved.channels.get(snowflake) if resolved.channels else None
    return channel.name if channel else None


async def handle_config_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle a component interaction from the config message."""
    custom_id = interaction.custom_id
    if not custom_id.startswith(SUBDAY_CONFIG_PREFIX):
        return

    guild_id = interaction.guild_id
    if not guild_id:
        return

    # Only allow the bot owner
    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    if not (bot.owner_ids and interaction.user.id in bot.owner_ids):
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="Only the bot owner can change these settings.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    field = custom_id.removeprefix(SUBDAY_CONFIG_PREFIX)
    guild_state = state.load(int(guild_id))
    cfg = guild_state.config

    new_value = _resolve_select_value(interaction, field)
    setattr(cfg, field, new_value)

    guild = await bot.rest.fetch_guild(guild_id)
    guild_state.guild_name = guild.name
    state.save(guild_state)
    logger.info(
        "G=%r U=%r: Updated SubDay config field %s",
        guild_state.guild_name,
        interaction.user.username,
        field,
    )

    # Update the message with new embed
    embed = _config_embed(cfg)
    embed.set_footer(text="Settings updated.")
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_UPDATE,
        embed=embed,
    )


def _register_config(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.add_checks(lightbulb.owner_only)
    @lightbulb.command(
        "config", "Configure SubDay settings for this server (owner only)"
    )
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_config(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        embed = _config_embed(cfg)
        components = _config_components(ctx.app)
        await ctx.respond(
            embed=embed,
            components=components,
            flags=hikari.MessageFlag.EPHEMERAL,
        )


def _register_prizes(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.add_checks(lightbulb.owner_only)
    @lightbulb.option(
        "prize_52", "Prize for week 52 graduation", type=str, required=False
    )
    @lightbulb.option(
        "prize_39", "Prize for week 39 milestone", type=str, required=False
    )
    @lightbulb.option(
        "prize_26", "Prize for week 26 milestone", type=str, required=False
    )
    @lightbulb.option(
        "prize_13", "Prize for week 13 milestone", type=str, required=False
    )
    @lightbulb.command("prizes", "Set milestone prize descriptions (owner only)")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_prizes(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        changed = False

        for field in ("prize_13", "prize_26", "prize_39", "prize_52"):
            value = getattr(ctx.options, field, None)
            if value is not None:
                setattr(cfg, field, value)
                changed = True

        if changed:
            guild = await ctx.app.rest.fetch_guild(ctx.guild_id)
            guild_state.guild_name = guild.name
            state.save(guild_state)
            logger.info(
                "G=%r U=%r: Updated SubDay prizes",
                guild_state.guild_name,
                ctx.author.username,
            )

        prizes = cfg.milestone_prizes()
        prize_lines = [f"**Week {w}:** {p}" for w, p in sorted(prizes.items())]
        embed = hikari.Embed(
            title="Where I am Led — Milestone Prizes",
            description="\n".join(prize_lines),
            color=SOLARIZED_VIOLET,
        )
        if changed:
            embed.set_footer(text="Prizes updated.")

        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
