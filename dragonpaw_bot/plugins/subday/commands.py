# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import logging
import re
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

MILESTONE_WEEKS = (13, 26, 39, 52)
TOTAL_WEEKS = 52
SUBDAY_SIGNUP_ID = "subday_signup"
WEEKS_DIR = Path(__file__).parent / "weeks"


# ---------------------------------------------------------------------------- #
#                                   Helpers                                    #
# ---------------------------------------------------------------------------- #


async def _get_guild(
    ctx: lightbulb.Context, bot: DragonpawBot
) -> hikari.Guild | hikari.RESTGuild:
    """Get guild from cache, falling back to REST fetch."""
    assert ctx.guild_id
    guild = ctx.get_guild()
    if guild:
        return guild
    logger.debug("G=%r: Guild not in cache, fetching via REST", ctx.guild_id)
    return await bot.rest.fetch_guild(ctx.guild_id)


async def _check_permission(
    ctx: lightbulb.Context,
    guild: hikari.Guild | hikari.RESTGuild,
    role_name: str | None,
    action: str,
) -> bool:
    """Check permission and respond with denial if lacking. Returns True if allowed."""
    assert ctx.member
    if utils.has_permission(guild, ctx.member, role_name):
        return True
    label = f"**{role_name}** role" if role_name else "server owner status"
    logger.warning(
        "G=%r U=%r: SubDay %s denied, missing %s",
        guild.name,
        ctx.author.username,
        action,
        label,
    )
    await ctx.respond(
        f"You need {label} to use this command.",
        flags=hikari.MessageFlag.EPHEMERAL,
    )
    return False


async def help_handler(ctx: lightbulb.Context) -> None:
    """Show contextual help listing commands the user can access."""
    assert ctx.guild_id
    bot: DragonpawBot = ctx.app  # type: ignore[assignment]
    guild = await _get_guild(ctx, bot)
    guild_state = state.load(int(ctx.guild_id))
    cfg = guild_state.config

    lines = [
        "`/subday about` — Learn about the program",
        "`/subday status` — Check your progress",
    ]
    if ctx.member:
        if utils.has_permission(guild, ctx.member, cfg.enroll_role):
            lines.append("`/subday signup` — Sign up for the program")
        if utils.has_permission(guild, ctx.member, cfg.complete_role):
            lines.append("`/subday complete @user` — Mark a week complete")
            lines.append("`/subday list` — View all participants")
            lines.append("`/subday remove @user` — Remove a participant")
        if utils.has_permission(guild, ctx.member, cfg.backfill_role):
            lines.append(
                "`/subday complete @user week:<n>` — Backfill to a specific week"
            )
    if ctx.app.owner_ids and ctx.author.id in ctx.app.owner_ids:
        lines.append("`/subday config` — Configure settings (bot owner)")
        lines.append("`/subday prize-roles` — Set milestone roles (bot owner)")
        lines.append("`/subday prizes` — Set milestone prizes (bot owner)")

    embed = hikari.Embed(
        title="Where I am Led — Commands",
        description="\n".join(lines),
        color=SOLARIZED_VIOLET,
    )
    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


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
    member: hikari.Member,
    week: int,
    role_name: str | None,
    prizes: dict[int, str],
) -> hikari.Embed:
    prize = prizes.get(week, "a prize")
    role_line = (
        f"⭐ They have earned the **{role_name}** role! ⭐\n\n" if role_name else ""
    )
    return hikari.Embed(
        title="🌟✨ Where I am Led — Milestone! ✨🌟",
        description=(
            f"🌟 {member.mention} has reached the **Week {week}** milestone "
            f"of Where I am Led! 🌟\n\n"
            f"{role_line}"
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
            f"🎁 Their prize: **{prizes.get(TOTAL_WEEKS, 'a prize')}**! 🎁\n\n"
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
    except hikari.HTTPError as exc:
        logger.warning(
            "U=%r: Cannot DM user for completion notice: %s", target.username, exc
        )


async def _notify_staff(
    bot: DragonpawBot,
    guild_id: hikari.Snowflake,
    staff_channel: str,
    message: str,
) -> None:
    """Send a message to the named staff channel. Logs a warning on failure."""
    channel = await utils.guild_channel_by_name(bot, guild_id, staff_channel)
    if not channel:
        logger.warning(
            "G=%r: No #%s channel found for staff notice", guild_id, staff_channel
        )
        return
    try:
        await channel.send(message)
    except hikari.HTTPError as exc:
        logger.warning(
            "G=%r: Failed to send staff notice to #%s: %s",
            guild_id,
            staff_channel,
            exc,
        )


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

    milestone_roles = cfg.milestone_roles()

    # Regular completion — just post the embed
    if week not in milestone_roles:
        if achievements:
            embed = _regular_completion_embed(target, week)
            embed.set_image("attachment://star_chart.png")
            await achievements.send(embed=embed, attachment=chart_bytes)
        return

    role_name = milestone_roles[week]

    # Milestone or graduation
    if week == TOTAL_WEEKS:
        logger.info("U=%r: GRADUATED from Where I am Led!", target.username)
        embed = _graduation_embed(target, prizes)
        staff_msg = (
            f"{completer.mention} — {target.mention} has **graduated** "
            f"from Where I am Led! Please arrange their prize: "
            f"**{prizes.get(TOTAL_WEEKS, 'a prize')}**."
        )
    else:
        logger.info(
            "U=%r: Reached milestone week %d (%s)",
            target.username,
            week,
            role_name or "no role",
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

    # Assign milestone role (skip if None)
    if role_name:
        role = await utils.guild_role_by_name(bot, guild_id, role_name)
        if role:
            await target.add_role(role, reason=f"SubDay: week {week}")
            logger.info("U=%r: Assigned role %r", target.username, role.name)
        else:
            logger.warning("Role %r not found in guild", role_name)

    # Notify staff
    if cfg.staff_channel:
        await _notify_staff(bot, guild_id, cfg.staff_channel, staff_msg)


# ---------------------------------------------------------------------------- #
#                              Shared signup logic                             #
# ---------------------------------------------------------------------------- #


async def _do_signup(
    bot: DragonpawBot,
    guild_id: hikari.Snowflake,
    user: hikari.User,
) -> str:
    """Run signup logic. Returns a response message string."""
    guild_state = state.load(int(guild_id))
    user_id = int(user.id)

    if user_id in guild_state.participants:
        logger.debug(
            "G=%r U=%r: SubDay signup rejected, already enrolled",
            guild_state.guild_name,
            user.username,
        )
        return "You are already signed up! Use `/subday status` to check your progress."

    participant = SubDayParticipant(
        user_id=user_id,
        signup_date=datetime.datetime.now(tz=datetime.UTC),
    )
    guild_state.participants[user_id] = participant

    guild = await bot.rest.fetch_guild(guild_id)
    guild_state.guild_name = guild.name
    state.save(guild_state)

    # DM week 1
    rules_text = prompts.load_rules()
    prompt = prompts.load_week(1)
    embed = prompts.build_prompt_embed(prompt)

    try:
        dm = await user.fetch_dm_channel()
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
        msg = (
            "You've been signed up for **Where I am Led**! "
            "Check your DMs for your first prompt."
        )
    except hikari.HTTPError as exc:
        logger.warning(
            "G=%r U=%r: Cannot DM user for subday signup: %s",
            guild.name,
            user.username,
            exc,
        )
        msg = (
            "You've been signed up for **Where I am Led**! "
            "However, I couldn't DM you your first prompt. "
            "Please enable DMs from server members and ask "
            "staff to have your prompt re-sent."
        )
    logger.info(
        "G=%r U=%r: Signed up for SubDay",
        guild.name,
        user.username,
    )
    return msg


async def handle_signup_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle the Sign Up button click from /subday about."""
    guild_id = interaction.guild_id
    if not guild_id or not interaction.member:
        logger.warning(
            "Signup interaction missing guild_id=%r or member=%r",
            interaction.guild_id,
            interaction.member,
        )
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="Something went wrong processing your signup. Please try again.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]

    guild_state = state.load(int(guild_id))
    cfg = guild_state.config

    guild = await bot.rest.fetch_guild(guild_id)
    if not utils.has_permission(guild, interaction.member, cfg.enroll_role):
        label = (
            f"**{cfg.enroll_role}** role" if cfg.enroll_role else "server owner status"
        )
        logger.warning(
            "G=%r U=%r: SubDay signup denied, missing %s",
            guild.name,
            interaction.user.username,
            label,
        )
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content=f"You need {label} to sign up.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    msg = await _do_signup(bot, guild_id, interaction.user)
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        content=msg,
        flags=hikari.MessageFlag.EPHEMERAL,
    )


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
    _register_config(subday_group)
    _register_prize_roles(subday_group)
    _register_prizes(subday_group)


def _register_about(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.command("about", "Learn about the Where I am Led program")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_about(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        logger.info(
            "U=%r: Viewed SubDay about",
            ctx.author.username,
        )

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        # Build template variables from guild config
        prizes = cfg.milestone_prizes()
        roles = cfg.milestone_roles()
        template_vars: dict[str, str] = {
            "enroll_role": cfg.enroll_role or "server owner",
            "complete_role": cfg.complete_role or "server owner",
        }
        reward_lines: list[str] = []
        for week in MILESTONE_WEEKS:
            line = f"**{week} weeks:** {prizes[week]}"
            role = roles.get(week)
            if role:
                line += f" + the **{role}** role"
            reward_lines.append(line)
            template_vars[f"prize_{week}"] = prizes[week]
            template_vars[f"role_{week}"] = role or ""
        template_vars["rewards"] = "\n".join(reward_lines)

        raw = (WEEKS_DIR / "about.md").read_text()

        def _replace_var(m: re.Match[str]) -> str:
            key = m.group(1)
            if key not in template_vars:
                logger.warning(
                    "G=%r: Unresolved template variable {{%s}} in about.md",
                    guild_state.guild_name,
                    key,
                )
                return m.group(0)
            return template_vars[key]

        text = re.sub(r"\{\{(\w+)\}\}", _replace_var, raw)

        title = "Where I am Led"
        for line in text.splitlines():
            if line.startswith("# ") and not line.startswith("## "):
                title = line[2:].strip()
                break

        sections = prompts.split_sections(text)
        embed = hikari.Embed(title=title, color=SOLARIZED_VIOLET)
        for heading, body in sections.items():
            embed.add_field(name=heading, value=body or "\u200b", inline=False)

        row = ctx.app.rest.build_message_action_row()
        row.add_interactive_button(
            hikari.ButtonStyle.SUCCESS, SUBDAY_SIGNUP_ID, label="Sign Up"
        )
        await ctx.respond(embed=embed, component=row)


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
        milestone_roles = guild_state.config.milestone_roles()
        next_milestone = None
        for m in sorted(milestone_roles):
            if m >= p.current_week:
                next_milestone = m
                break

        if next_milestone:
            role_name = milestone_roles[next_milestone]
            role_label = f" ({role_name})" if role_name else ""
            weeks_away = next_milestone - p.current_week
            if weeks_away == 0:
                milestone_text = f"This is a milestone week!{role_label}"
            else:
                milestone_text = (
                    f"Next milestone: **Week {next_milestone}**"
                    f"{role_label} — "
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
        guild = await _get_guild(ctx, bot)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await _check_permission(ctx, guild, cfg.enroll_role, "signup"):
            return

        msg = await _do_signup(bot, ctx.guild_id, ctx.author)
        await ctx.respond(msg, flags=hikari.MessageFlag.EPHEMERAL)


def _prepare_backfill(
    guild_state: state.SubDayGuildState,
    target_id: int,
    week: int,
) -> tuple[SubDayParticipant, bool]:
    """Auto-enroll if needed and set the participant's week. Returns (participant, auto_enrolled)."""
    if target_id in guild_state.participants:
        participant = guild_state.participants[target_id]
        auto_enrolled = False
    else:
        participant = SubDayParticipant(
            user_id=target_id,
            signup_date=datetime.datetime.now(tz=datetime.UTC),
        )
        guild_state.participants[target_id] = participant
        auto_enrolled = True

    participant.current_week = week
    participant.week_sent = False
    return participant, auto_enrolled


def _validate_normal_complete(
    guild_state: state.SubDayGuildState,
    target: hikari.Member,
    target_id: int,
) -> str | None:
    """Validate that a normal (non-backfill) completion is possible. Returns error message or None."""
    if target_id not in guild_state.participants:
        return f"{target.mention} is not signed up for Where I am Led."

    participant = guild_state.participants[target_id]

    if participant.week_completed:
        return (
            f"{target.mention} has already completed week "
            f"{participant.current_week}. "
            "They'll receive their next prompt on Sunday."
        )

    if participant.current_week > TOTAL_WEEKS:
        return f"{target.mention} has already graduated!"

    return None


def _register_complete(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.option(
        "week",
        "Backfill: set to this week and mark complete",
        type=int,
        min_value=1,
        max_value=TOTAL_WEEKS,
        required=False,
    )
    @lightbulb.option("user", "The participant to mark complete", type=hikari.Member)
    @lightbulb.command("complete", "Mark a participant's current week as complete")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_complete(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member
        bot: DragonpawBot = ctx.app  # type: ignore[assignment]
        guild = await _get_guild(ctx, bot)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        target: hikari.Member = ctx.options.user
        target_id = int(target.id)

        # If the requested week matches the participant's current week, fall back
        # to normal complete so the reviewer only needs complete_role, not backfill_role.
        backfill_week: int | None = ctx.options.week
        existing = guild_state.participants.get(target_id)
        if backfill_week and existing and existing.current_week == backfill_week:
            backfill_week = None

        required_role = cfg.backfill_role if backfill_week else cfg.complete_role
        action = "backfill" if backfill_week else "complete"

        if not await _check_permission(ctx, guild, required_role, action):
            return

        # Prevent self-completion
        if target.id == ctx.author.id:
            logger.debug("U=%r: SubDay self-completion blocked", ctx.author.username)
            await ctx.respond(
                "You cannot mark your own work as complete.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        if backfill_week:
            result = _prepare_backfill(guild_state, target_id, backfill_week)
            participant, auto_enrolled = result
        else:
            error = _validate_normal_complete(guild_state, target, target_id)
            if error:
                await ctx.respond(error, flags=hikari.MessageFlag.EPHEMERAL)
                return
            participant = guild_state.participants[target_id]
            auto_enrolled = False

        week = participant.current_week
        participant.week_completed = True
        participant.last_completed_date = datetime.datetime.now(tz=datetime.UTC)
        state.save(guild_state)

        await _post_achievement(bot, ctx.guild_id, ctx.member, target, week, cfg)

        # Notify staff channel of completion (milestones are already notified
        # by _post_achievement, so only send for regular completions/backfills)
        milestone_roles = cfg.milestone_roles()
        if cfg.staff_channel and week not in milestone_roles:
            if backfill_week:
                staff_msg = (
                    f"{ctx.member.mention} backfilled {target.mention} "
                    f"to **Week {week}** (complete)."
                )
            else:
                staff_msg = (
                    f"{ctx.member.mention} marked {target.mention} "
                    f"complete for **Week {week}**."
                )
            await _notify_staff(bot, ctx.guild_id, cfg.staff_channel, staff_msg)

        if auto_enrolled:
            response = (
                f"Enrolled {target.mention} and completed "
                f"**Week {week}** of Where I am Led."
            )
        else:
            response = f"Marked {target.mention} as complete for **Week {week}**."

        await ctx.respond(response, flags=hikari.MessageFlag.EPHEMERAL)
        logger.info(
            "G=%r U=%r: Completed SubDay week %d (marked by %s%s)",
            guild_state.guild_name,
            target.username,
            week,
            ctx.author.username,
            ", backfill" if backfill_week else "",
        )


def _register_list(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.command("list", "List all participants and their progress")
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_list(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member
        bot: DragonpawBot = ctx.app  # type: ignore[assignment]
        guild = await _get_guild(ctx, bot)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await _check_permission(ctx, guild, cfg.complete_role, "list"):
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
        bot: DragonpawBot = ctx.app  # type: ignore[assignment]
        guild = await _get_guild(ctx, bot)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await _check_permission(ctx, guild, cfg.complete_role, "remove"):
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


SUBDAY_CONFIG_PREFIX = "subday_cfg:"
SUBDAY_CFG_ROLE_PREFIX = "subday_cfg_role:"


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
    roles = cfg.milestone_roles()
    role_lines = [
        f"**Week {w}:** `{r}`" if r else f"**Week {w}:** _None_"
        for w, r in sorted(roles.items())
    ]
    embed.add_field(
        name="Milestone roles",
        value="\n".join(role_lines),
        inline=False,
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
        placeholder="Backfill role (who can backfill weeks)",
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
        placeholder="Staff channel (completions and milestones)",
        min_values=0,
        max_values=1,
    )
    rows.append(row5)

    return rows


ROLE_FIELDS = {
    "enroll_role",
    "complete_role",
    "backfill_role",
    "role_13",
    "role_26",
    "role_39",
    "role_52",
}


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
    if field in ROLE_FIELDS:
        role = resolved.roles.get(snowflake) if resolved.roles else None
        return role.name if role else None
    channel = resolved.channels.get(snowflake) if resolved.channels else None
    return channel.name if channel else None


async def handle_config_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle a component interaction from the config or prize-roles message."""
    custom_id = interaction.custom_id

    if custom_id.startswith(SUBDAY_CFG_ROLE_PREFIX):
        field = custom_id.removeprefix(SUBDAY_CFG_ROLE_PREFIX)
        embed_builder = _prize_roles_embed
    elif custom_id.startswith(SUBDAY_CONFIG_PREFIX):
        field = custom_id.removeprefix(SUBDAY_CONFIG_PREFIX)
        embed_builder = _config_embed
    else:
        return

    guild_id = interaction.guild_id
    if not guild_id:
        logger.warning("Config interaction missing guild_id, custom_id=%r", custom_id)
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="This command must be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
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

    guild_state = state.load(int(guild_id))
    cfg = guild_state.config
    old_value = getattr(cfg, field)

    new_value = _resolve_select_value(interaction, field)

    # For channel fields, verify the bot can write to the selected channel
    if new_value and field in ("achievements_channel", "staff_channel"):
        channel_id = hikari.Snowflake(interaction.values[0])
        missing = await utils.check_channel_perms(bot, guild_id, channel_id)
        if missing:
            missing_str = ", ".join(f"**{p}**" for p in missing)
            await interaction.create_initial_response(
                response_type=hikari.ResponseType.MESSAGE_CREATE,
                content=(
                    f"I can't use #{new_value} — I'm missing these permissions: "
                    f"{missing_str}. Please fix the channel permissions and try again."
                ),
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            logger.warning(
                "G=%r U=%r: SubDay config rejected #%s — missing permissions: %s",
                guild_state.guild_name,
                interaction.user.username,
                new_value,
                ", ".join(missing),
            )
            return

    setattr(cfg, field, new_value)

    guild = await bot.rest.fetch_guild(guild_id)
    guild_state.guild_name = guild.name
    state.save(guild_state)

    if new_value != old_value:
        display_old = old_value or "None"
        display_new = new_value or "None"
        logger.info(
            "G=%r U=%r: SubDay config %s: %s -> %s",
            guild_state.guild_name,
            interaction.user.username,
            field,
            display_old,
            display_new,
        )

    # Update the message with new embed
    embed = embed_builder(cfg)
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


def _prize_roles_embed(cfg: SubDayGuildConfig) -> hikari.Embed:
    """Build an embed showing current milestone role settings."""
    embed = hikari.Embed(
        title="Where I am Led — Milestone Roles",
        description=(
            "Use the dropdowns below to set milestone roles. "
            "Deselect to disable role assignment for that milestone."
        ),
        color=SOLARIZED_VIOLET,
    )
    for week in MILESTONE_WEEKS:
        role_name = getattr(cfg, f"role_{week}")
        embed.add_field(
            name=f"Week {week}",
            value=f"`{role_name}`" if role_name else "_None (no role granted)_",
            inline=True,
        )
    return embed


def _prize_roles_components(
    bot: lightbulb.BotApp,
) -> list[hikari.api.ComponentBuilder]:
    """Build the action rows for the prize-roles message."""
    rows: list[hikari.api.ComponentBuilder] = []
    for week in MILESTONE_WEEKS:
        row = bot.rest.build_message_action_row()
        row.add_select_menu(
            hikari.ComponentType.ROLE_SELECT_MENU,
            f"{SUBDAY_CFG_ROLE_PREFIX}role_{week}",
            placeholder=f"Week {week} milestone role",
            min_values=0,
            max_values=1,
        )
        rows.append(row)
    return rows


def _register_prize_roles(subday_group: lightbulb.SlashCommandGroup) -> None:
    @subday_group.child
    @lightbulb.add_checks(lightbulb.owner_only)
    @lightbulb.command(
        "prize-roles",
        "Configure milestone roles for this server (owner only)",
    )
    @lightbulb.implements(lightbulb.SlashSubCommand)
    async def subday_prize_roles(ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        embed = _prize_roles_embed(cfg)
        components = _prize_roles_components(ctx.app)
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
                old_value = getattr(cfg, field)
                setattr(cfg, field, value)
                changed = True
                logger.info(
                    "G=%r U=%r: SubDay %s: %s -> %s",
                    guild_state.guild_name,
                    ctx.author.username,
                    field,
                    old_value,
                    value,
                )

        if changed:
            guild = await ctx.app.rest.fetch_guild(ctx.guild_id)
            guild_state.guild_name = guild.name
            state.save(guild_state)

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
