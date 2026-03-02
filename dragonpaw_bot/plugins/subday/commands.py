# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING

import hikari
import lightbulb

from dragonpaw_bot import utils
from dragonpaw_bot.colors import (
    SOLARIZED_CYAN,
    SOLARIZED_MAGENTA,
    SOLARIZED_VIOLET,
    SOLARIZED_YELLOW,
)
from dragonpaw_bot.plugins.subday import chart, prompts, state
from dragonpaw_bot.plugins.subday.constants import (
    MAX_EMBEDS_PER_MESSAGE,
    MILESTONE_WEEKS,
    SUBDAY_CFG_ROLE_PREFIX,
    SUBDAY_CONFIG_PREFIX,
    SUBDAY_OWNER_APPROVE_PREFIX,
    SUBDAY_OWNER_DENY_PREFIX,
    SUBDAY_SIGNUP_ID,
    TOTAL_WEEKS,
)
from dragonpaw_bot.plugins.subday.models import SubDayGuildConfig, SubDayParticipant

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------- #
#                                   Helpers                                    #
# ---------------------------------------------------------------------------- #


def _get_bot(ctx: lightbulb.Context) -> DragonpawBot:
    bot: DragonpawBot = ctx.client.app  # type: ignore[assignment]
    return bot


async def _check_permission(
    ctx: lightbulb.Context,
    guild: hikari.Guild | hikari.RESTGuild,
    role_name: str | list[str] | None,
    action: str,
) -> bool:
    """Check permission and respond with denial if lacking. Returns True if allowed."""
    assert ctx.member
    if isinstance(role_name, list):
        allowed = utils.has_any_role_permission(guild, ctx.member, role_name)
        label = (
            "one of the **" + "**, **".join(role_name) + "** roles"
            if role_name
            else "server owner status"
        )
    else:
        allowed = utils.has_permission(guild, ctx.member, role_name)
        label = f"**{role_name}** role" if role_name else "server owner status"
    if allowed:
        return True
    logger.warning(
        "G=%r U=%r: SubDay %s denied, missing %s",
        guild.name,
        ctx.user.username,
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
    bot = _get_bot(ctx)
    guild = await utils.get_guild(ctx, bot)
    guild_state = state.load(int(ctx.guild_id))
    cfg = guild_state.config

    lines = [
        "`/subday about` — Learn about the program",
        "`/subday status` — Check your progress (and your subs')",
        "`/subday owner` — Set or clear your owner",
    ]
    if ctx.member:
        if utils.has_any_role_permission(guild, ctx.member, cfg.enroll_role):
            lines.append("`/subday signup` — Sign up for the program")
        if utils.has_permission(guild, ctx.member, cfg.complete_role):
            lines.append("`/subday complete @user` — Mark a week complete")
            lines.append("`/subday list` — View all participants")
            lines.append("`/subday remove @user` — Remove a participant")
        if utils.has_permission(guild, ctx.member, cfg.backfill_role):
            lines.append(
                "`/subday complete @user week:<n>` — Backfill to a specific week"
            )
    if bot.owner_ids and ctx.user.id in bot.owner_ids:
        lines.append("`/subday config` — Configure settings (server owner)")
        lines.append("`/subday prize-roles` — Set milestone roles (server owner)")
        lines.append("`/subday prizes` — Set milestone prizes (server owner)")

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
        embed.set_image(chart_bytes)
        await dm.send(embed=embed)
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


async def _post_achievement(  # noqa: PLR0912, PLR0913
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

    # Generate star chart attachment (use guild display name)
    chart_bytes = chart.render_star_chart(
        username=target.display_name,
        current_week=week,
        week_completed=True,
    )

    await _dm_completion(target, week, chart_bytes)

    milestone_roles = cfg.milestone_roles()

    # Regular completion — just post the embed
    if week not in milestone_roles:
        if achievements:
            try:
                embed = _regular_completion_embed(target, week)
                embed.set_image(chart_bytes)
                await achievements.send(embed=embed)
            except hikari.HTTPError as exc:
                logger.warning(
                    "G=%r: Failed to post achievement: %s",
                    cfg.achievements_channel,
                    exc,
                )
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
        try:
            embed.set_image(chart_bytes)
            await achievements.send(embed=embed)
        except hikari.HTTPError as exc:
            logger.warning(
                "G=%r: Failed to post milestone achievement: %s",
                cfg.achievements_channel,
                exc,
            )

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
    prompt_embed = prompts.build_prompt_embed(prompt)

    welcome_embed = hikari.Embed(
        title="🎉 Welcome to Where I am Led!",
        description=(
            "You've just embarked on a **52-week** guided journal journey "
            "exploring the psychology and mindset of service.\n\n"
            f"📋 **Instructions:**\n{rules_text}\n\n"
            "Each Sunday you'll receive a new prompt right here in your DMs. "
            "Take your time — there are no deadlines! 💜\n\n"
            "Here's your first week's prompt below. Good luck! ✨"
        ),
        color=SOLARIZED_CYAN,
    )

    try:
        dm = await user.fetch_dm_channel()
        await dm.send(embeds=[welcome_embed, prompt_embed])
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
    if not utils.has_any_role_permission(guild, interaction.member, cfg.enroll_role):
        label = (
            "one of the **" + "**, **".join(cfg.enroll_role) + "** roles"
            if cfg.enroll_role
            else "server owner status"
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


def register(subday_group: lightbulb.Group) -> None:
    """Register all subcommands on the given command group."""
    subday_group.register(SubDayAbout)
    subday_group.register(SubDayStatus)
    subday_group.register(SubDayOwner)
    subday_group.register(SubDaySignup)
    subday_group.register(SubDayComplete)
    subday_group.register(SubDayList)
    subday_group.register(SubDayRemove)
    subday_group.register(SubDayConfig)
    subday_group.register(SubDayPrizeRoles)
    subday_group.register(SubDayPrizes)


class SubDayAbout(
    lightbulb.SlashCommand,
    name="about",
    description="Learn about the Where I am Led program",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        logger.info(
            "U=%r: Viewed SubDay about",
            ctx.user.username,
        )

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        enroll_label = ", ".join(cfg.enroll_role) if cfg.enroll_role else "server owner"

        # -- Embed 1: Introduction (violet) --
        intro = hikari.Embed(
            title="Where I am Led — Weekly Journaling",
            color=SOLARIZED_VIOLET,
        )
        intro.add_field(
            name="📖 What's this all about?",
            value=(
                f"For folks with the **{enroll_label}** role "
                "(with their Owner's permission, if Owned), we invite you to "
                'participate in our weekly "Subday" (Sunday) journaling.\n\n'
                "It's based on *Where I am Led* by Christina Parker — a set of "
                "weekly prompts exploring the psychology and mindset of a service "
                "submissive. We strongly encourage all subs to give it a try."
            ),
            inline=False,
        )
        intro.add_field(
            name="✍️ What does it involve?",
            value=(
                "Each week has four short prompts. Write as little or as much "
                "as you like exploring your feelings on D/s and that week's "
                "topics. We'll reach out to each new sub as you join and help "
                "get you started."
            ),
            inline=False,
        )
        intro.add_field(
            name="🙈 Do I have to write?",
            value=(
                "Nope! Paper, a notes app, a text file, DM your answers to "
                "your Owner — anything goes. Just jot it on a napkin if you "
                "want.\n\n"
                "_(If we could, we'd slap every dom who ever assigned lines "
                "as a punishment and traumatized submissives to think of "
                "writing as punishment. —Ash)_"
            ),
            inline=False,
        )

        # -- Embed 2: Details (cyan) --
        details = hikari.Embed(
            title="The Details",
            color=SOLARIZED_CYAN,
        )
        details.add_field(
            name="🔒 Will anyone see my answers?",
            value=(
                "Your Owner will, if you have one. No one else does. Your Owner "
                "will check in with staff so they can track your progress for "
                "rewards. If you're not Owned, check in with a member of staff "
                "yourself."
            ),
            inline=False,
        )
        details.add_field(
            name="⏰ I hate deadlines!",
            value=(
                "You're in luck — there aren't any. Each week takes about "
                "15–30 minutes, but take as long as you need. You just can't "
                "get the next week until you've completed the current one."
            ),
            inline=False,
        )

        # -- Embed 3: Rewards & signup (yellow) --
        prizes = cfg.milestone_prizes()
        roles = cfg.milestone_roles()
        reward_lines: list[str] = []
        for week in MILESTONE_WEEKS:
            line = f"**{week} weeks:** {prizes[week]}"
            role = roles.get(week)
            if role:
                line += f" + the **{role}** role"
            reward_lines.append(line)

        signup = hikari.Embed(
            title="Rewards & Getting Started",
            color=SOLARIZED_YELLOW,
        )
        signup.add_field(
            name="🎁 Rewards",
            value="\n".join(reward_lines),
            inline=False,
        )
        signup.add_field(
            name="🚀 How do I start?",
            value=(
                "Use the `/subday signup` command, or press the "
                "**Sign Up** button below! You'll get your first week's "
                "prompt sent to your DMs automatically."
            ),
            inline=False,
        )

        bot = _get_bot(ctx)
        row = bot.rest.build_message_action_row()
        row.add_interactive_button(
            hikari.ButtonStyle.SUCCESS, SUBDAY_SIGNUP_ID, label="Sign Up"
        )
        await ctx.respond(
            embeds=[intro, details, signup],
            component=row,
            flags=hikari.MessageFlag.EPHEMERAL,
        )


def _owned_sub_status_embed(
    sub_user_id: int, p: SubDayParticipant, cfg: SubDayGuildConfig
) -> hikari.Embed:
    """Build a compact status embed for an owned sub (no star chart)."""
    if p.current_week > TOTAL_WEEKS:
        icon = "🎓"
        status = "Graduated!"
    elif p.week_completed:
        icon = "✅"
        status = f"Week {p.current_week} — completed"
    else:
        icon = "⏳"
        status = f"Week {p.current_week} — in progress"

    # Next prize teaser
    prizes = cfg.milestone_prizes()
    next_milestone = next((m for m in MILESTONE_WEEKS if m >= p.current_week), None)
    if next_milestone:
        prize = prizes.get(next_milestone, "a prize")
        weeks_away = next_milestone - p.current_week
        if weeks_away == 0:
            status += f"\n🎁 Milestone week! Prize: **{prize}**"
        else:
            status += f"\n🎁 Next prize ({weeks_away}w away): **{prize}**"

    embed = hikari.Embed(
        title=f"{icon} <@{sub_user_id}>",
        description=status,
        color=SOLARIZED_CYAN,
    )
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
    return embed


def _own_progress_embed(
    p: SubDayParticipant, display_name: str, cfg: SubDayGuildConfig
) -> hikari.Embed:
    """Build the caller's own progress embed with star chart."""
    if p.current_week > TOTAL_WEEKS:
        chart_bytes = chart.render_star_chart(
            username=display_name,
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
        embed.set_image(chart_bytes)
        return embed

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

    milestone_roles = cfg.milestone_roles()
    next_milestone = next(
        (m for m in sorted(milestone_roles) if m >= p.current_week), None
    )
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

    # Next prize teaser
    prizes = cfg.milestone_prizes()
    prize_milestone = next((m for m in MILESTONE_WEEKS if m >= p.current_week), None)
    if prize_milestone:
        prize = prizes.get(prize_milestone, "a prize")
        pw = prize_milestone - p.current_week
        if pw == 0:
            status_text += f"\n🎁 Milestone week! Prize: **{prize}**"
        else:
            status_text += f"\n🎁 Next prize ({pw}w away): **{prize}**"

    chart_bytes = chart.render_star_chart(
        username=display_name,
        current_week=p.current_week,
        week_completed=p.week_completed,
    )

    embed = hikari.Embed(
        title="Where I am Led — Your Progress",
        description=status_text,
        color=SOLARIZED_VIOLET,
    )
    embed.set_image(chart_bytes)
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
    return embed


class SubDayStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Check your own progress in Where I am Led",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        logger.info("U=%r: Checking SubDay status", ctx.user.username)

        guild_state = state.load(int(ctx.guild_id))
        user_id = int(ctx.user.id)

        own_participant = guild_state.participants.get(user_id)
        owned_subs = [
            (uid, p)
            for uid, p in guild_state.participants.items()
            if p.owner_id == user_id
        ]

        if not own_participant and not owned_subs:
            await ctx.respond(
                "You're not signed up for Where I am Led. "
                "Use `/subday signup` to get started!",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        cfg = guild_state.config
        embeds: list[hikari.Embed] = []

        if own_participant:
            display_name = ctx.member.display_name if ctx.member else ctx.user.username
            embeds.append(_own_progress_embed(own_participant, display_name, cfg))

        for sub_uid, sub_p in owned_subs:
            if len(embeds) >= MAX_EMBEDS_PER_MESSAGE:
                break
            embeds.append(_owned_sub_status_embed(sub_uid, sub_p, cfg))

        await ctx.respond(
            embeds=embeds,
            flags=hikari.MessageFlag.EPHEMERAL,
        )


class SubDayOwner(
    lightbulb.SlashCommand,
    name="owner",
    description="Set or clear your owner (they receive copies of your prompts)",
):
    user = lightbulb.user("user", "Your owner (leave blank to clear)", default=None)

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild_state = state.load(int(ctx.guild_id))
        user_id = int(ctx.user.id)

        if user_id not in guild_state.participants:
            await ctx.respond(
                "You must be signed up for Where I am Led to use this command. "
                "Use `/subday signup` to get started!",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        participant = guild_state.participants[user_id]
        target: hikari.User | None = self.user  # type: ignore[assignment]

        # Clear owner
        if target is None:
            had_owner = participant.owner_id is not None
            had_pending = participant.pending_owner_id is not None
            participant.owner_id = None
            participant.pending_owner_id = None
            if had_owner or had_pending:
                state.save(guild_state)
                logger.info(
                    "G=%r U=%r: Cleared owner",
                    guild_state.guild_name,
                    ctx.user.username,
                )
            await ctx.respond(
                "Your owner has been cleared.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        target_id = int(target.id)

        # Can't set self
        if target_id == user_id:
            await ctx.respond(
                "You can't set yourself as your own owner.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        # Already confirmed owner
        if participant.owner_id == target_id:
            await ctx.respond(
                f"{target.mention} is already your owner.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        # Send approval DM to the target
        participant.pending_owner_id = target_id

        guild = await bot.rest.fetch_guild(ctx.guild_id)
        row = bot.rest.build_message_action_row()
        row.add_interactive_button(
            hikari.ButtonStyle.SUCCESS,
            f"{SUBDAY_OWNER_APPROVE_PREFIX}{ctx.guild_id}:{user_id}",
            label="Accept",
        )
        row.add_interactive_button(
            hikari.ButtonStyle.DANGER,
            f"{SUBDAY_OWNER_DENY_PREFIX}{ctx.guild_id}:{user_id}",
            label="Decline",
        )

        try:
            dm = await target.fetch_dm_channel()
            await dm.send(
                content=(
                    f"**{ctx.user.mention}** in **{guild.name}** has asked you to be their "
                    f"owner for the **Where I am Led** journal program.\n\n"
                    "As their owner, you'll receive copies of their weekly prompts "
                    "and can check their progress with `/subday status`."
                ),
                component=row,
            )
        except (hikari.ForbiddenError, hikari.HTTPError) as exc:
            participant.pending_owner_id = None
            state.save(guild_state)
            logger.warning(
                "G=%r U=%r: Failed to DM owner request to %r: %s",
                guild_state.guild_name,
                ctx.user.username,
                target.username,
                exc,
            )
            await ctx.respond(
                f"I couldn't send a DM to {target.mention}. "
                "They may have DMs disabled. The request was not sent.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        state.save(guild_state)
        logger.info(
            "G=%r U=%r: Sent owner request to %r",
            guild_state.guild_name,
            ctx.user.username,
            target.username,
        )
        await ctx.respond(
            f"An owner request has been sent to {target.mention}. "
            "They'll need to accept it via DM.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


async def handle_owner_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle Accept/Decline button clicks for owner requests."""
    cid = interaction.custom_id
    owner_user_id = int(interaction.user.id)

    if cid.startswith(SUBDAY_OWNER_APPROVE_PREFIX):
        parts = cid.removeprefix(SUBDAY_OWNER_APPROVE_PREFIX).split(":")
        approve = True
    elif cid.startswith(SUBDAY_OWNER_DENY_PREFIX):
        parts = cid.removeprefix(SUBDAY_OWNER_DENY_PREFIX).split(":")
        approve = False
    else:
        return

    guild_id = int(parts[0])
    sub_user_id = int(parts[1])

    guild_state = state.load(guild_id)
    participant = guild_state.participants.get(sub_user_id)

    if not participant:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="This person is no longer enrolled in the program.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # Check if this request is still valid
    if participant.pending_owner_id != owner_user_id:
        # Double-click case: already approved
        if participant.owner_id == owner_user_id:
            await interaction.create_initial_response(
                response_type=hikari.ResponseType.MESSAGE_CREATE,
                content="You're already their owner!",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="This request is no longer valid (they may have changed their mind).",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]

    if approve:
        # Verify owner is still in the guild
        try:
            await bot.rest.fetch_member(
                hikari.Snowflake(guild_id), hikari.Snowflake(owner_user_id)
            )
        except hikari.NotFoundError:
            participant.pending_owner_id = None
            state.save(guild_state)
            await interaction.create_initial_response(
                response_type=hikari.ResponseType.MESSAGE_CREATE,
                content="You're no longer in that server, so the request has been cancelled.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        participant.owner_id = owner_user_id
        participant.pending_owner_id = None
        state.save(guild_state)

        logger.info(
            "G=%r: Owner approved — owner=%d sub=%d",
            guild_state.guild_name,
            owner_user_id,
            sub_user_id,
        )

        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content=f"You've accepted! You're now the owner for <@{sub_user_id}>.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        # Notify sub
        try:
            sub_user = await bot.rest.fetch_user(hikari.Snowflake(sub_user_id))
            dm = await sub_user.fetch_dm_channel()
            await dm.send(
                f"<@{owner_user_id}> has **accepted** your owner request! "
                "They'll now receive copies of your weekly prompts. 💜"
            )
        except hikari.HTTPError:
            logger.debug("Could not DM sub %d about owner approval", sub_user_id)
    else:
        participant.pending_owner_id = None
        state.save(guild_state)

        logger.info(
            "G=%r: Owner denied — owner=%d sub=%d",
            guild_state.guild_name,
            owner_user_id,
            sub_user_id,
        )

        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="You've declined the request.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        # Notify sub
        try:
            sub_user = await bot.rest.fetch_user(hikari.Snowflake(sub_user_id))
            dm = await sub_user.fetch_dm_channel()
            await dm.send(f"<@{owner_user_id}> has **declined** your owner request.")
        except hikari.HTTPError:
            logger.debug("Could not DM sub %d about owner denial", sub_user_id)


class SubDaySignup(
    lightbulb.SlashCommand,
    name="signup",
    description="Sign up for the Where I am Led program",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await _check_permission(ctx, guild, cfg.enroll_role, "signup"):
            return

        msg = await _do_signup(bot, ctx.guild_id, ctx.user)
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


class SubDayComplete(
    lightbulb.SlashCommand,
    name="complete",
    description="Mark a participant's current week as complete",
):
    user = lightbulb.user("user", "The participant to mark complete")
    week = lightbulb.integer(
        "week",
        "Backfill: set to this week and mark complete",
        min_value=1,
        max_value=TOTAL_WEEKS,
        default=None,
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        target: hikari.Member = self.user  # type: ignore[assignment]
        target_id = int(target.id)

        # If the requested week matches the participant's current week, fall back
        # to normal complete so the reviewer only needs complete_role, not backfill_role.
        backfill_week: int | None = self.week
        existing = guild_state.participants.get(target_id)
        if backfill_week and existing and existing.current_week == backfill_week:
            backfill_week = None

        required_role = cfg.backfill_role if backfill_week else cfg.complete_role
        action = "backfill" if backfill_week else "complete"

        if not await _check_permission(ctx, guild, required_role, action):
            return

        # Prevent self-completion
        if target.id == ctx.user.id:
            logger.debug("U=%r: SubDay self-completion blocked", ctx.user.username)
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
            ctx.user.username,
            ", backfill" if backfill_week else "",
        )


class SubDayList(
    lightbulb.SlashCommand,
    name="list",
    description="List all participants and their progress",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await _check_permission(ctx, guild, cfg.complete_role, "list"):
            return

        logger.info(
            "G=%r U=%r: Listing SubDay participants (%d enrolled)",
            guild_state.guild_name,
            ctx.user.username,
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


class SubDayRemove(
    lightbulb.SlashCommand,
    name="remove",
    description="Remove a participant from the program",
):
    user = lightbulb.user("user", "The participant to remove")

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member
        bot = _get_bot(ctx)
        guild = await utils.get_guild(ctx, bot)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await _check_permission(ctx, guild, cfg.complete_role, "remove"):
            return

        target: hikari.Member = self.user  # type: ignore[assignment]
        target_id = int(target.id)

        if target_id not in guild_state.participants:
            await ctx.respond(
                f"{target.mention} is not signed up for Where I am Led.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        del guild_state.participants[target_id]

        # Clean up owner references pointing to the removed user
        for p in guild_state.participants.values():
            if p.owner_id == target_id:
                p.owner_id = None
            if p.pending_owner_id == target_id:
                p.pending_owner_id = None

        state.save(guild_state)

        await ctx.respond(
            f"Removed {target.mention} from Where I am Led.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        logger.info(
            "G=%r U=%r: Removed from SubDay by %s",
            guild_state.guild_name,
            target.username,
            ctx.user.username,
        )


def _config_embed(cfg: SubDayGuildConfig) -> hikari.Embed:
    """Build an embed showing current SubDay config settings."""
    embed = hikari.Embed(
        title="Where I am Led — Configuration",
        description="Use the dropdowns below to change settings. Deselect to clear.",
        color=SOLARIZED_VIOLET,
    )
    embed.add_field(
        name="Enroll role(s)",
        value=", ".join(f"`{r}`" for r in cfg.enroll_role)
        if cfg.enroll_role
        else "_Owner-only_",
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


class _DefaultsActionRow:
    """Wraps a MessageActionRowBuilder to inject default_values into the payload.

    Hikari's select menu builders don't emit ``default_values`` for
    auto-populated selects (role/channel).  Discord's API *does* support
    the field, so we patch it into the built dict.
    """

    def __init__(
        self,
        inner: hikari.api.ComponentBuilder,
        defaults: list[dict[str, str]],
    ) -> None:
        self._inner = inner
        self._defaults = defaults

    def build(self) -> tuple[dict, list]:  # noqa: ANN401
        payload, resources = self._inner.build()
        if self._defaults:
            payload["components"][0]["default_values"] = self._defaults
        return payload, resources


async def _config_components(
    bot: DragonpawBot,
    guild_id: hikari.Snowflakeish,
    cfg: SubDayGuildConfig,
) -> list[hikari.api.ComponentBuilder]:
    """Build the action rows for the config message with current values pre-selected."""
    roles = await bot.rest.fetch_roles(guild_id)
    channels = await bot.rest.fetch_guild_channels(guild_id)
    role_map = {r.name: r.id for r in roles}
    channel_map = {c.name: c.id for c in channels if hasattr(c, "name")}

    rows: list[hikari.api.ComponentBuilder] = []

    # Row 1: Enroll role select (multi-select)
    row1 = bot.rest.build_message_action_row()
    row1.add_select_menu(
        hikari.ComponentType.ROLE_SELECT_MENU,
        f"{SUBDAY_CONFIG_PREFIX}enroll_role",
        placeholder="Enroll role(s) (who can sign up)",
        min_values=0,
        max_values=25,
    )
    defaults1 = [
        {"id": str(role_map[name]), "type": "role"}
        for name in cfg.enroll_role
        if name in role_map
    ]
    rows.append(_DefaultsActionRow(row1, defaults1))

    # Row 2: Complete role select
    row2 = bot.rest.build_message_action_row()
    row2.add_select_menu(
        hikari.ComponentType.ROLE_SELECT_MENU,
        f"{SUBDAY_CONFIG_PREFIX}complete_role",
        placeholder="Complete role (who can complete/list/remove)",
        min_values=0,
        max_values=1,
    )
    defaults2 = (
        [{"id": str(role_map[cfg.complete_role]), "type": "role"}]
        if cfg.complete_role and cfg.complete_role in role_map
        else []
    )
    rows.append(_DefaultsActionRow(row2, defaults2))

    # Row 3: Backfill role select
    row3 = bot.rest.build_message_action_row()
    row3.add_select_menu(
        hikari.ComponentType.ROLE_SELECT_MENU,
        f"{SUBDAY_CONFIG_PREFIX}backfill_role",
        placeholder="Backfill role (who can backfill weeks)",
        min_values=0,
        max_values=1,
    )
    defaults3 = (
        [{"id": str(role_map[cfg.backfill_role]), "type": "role"}]
        if cfg.backfill_role and cfg.backfill_role in role_map
        else []
    )
    rows.append(_DefaultsActionRow(row3, defaults3))

    # Row 4: Achievements channel select
    row4 = bot.rest.build_message_action_row()
    row4.add_channel_menu(
        f"{SUBDAY_CONFIG_PREFIX}achievements_channel",
        channel_types=[hikari.ChannelType.GUILD_TEXT],
        placeholder="Achievements channel (public posts)",
        min_values=0,
        max_values=1,
    )
    defaults4 = (
        [{"id": str(channel_map[cfg.achievements_channel]), "type": "channel"}]
        if cfg.achievements_channel and cfg.achievements_channel in channel_map
        else []
    )
    rows.append(_DefaultsActionRow(row4, defaults4))

    # Row 5: Staff channel select
    row5 = bot.rest.build_message_action_row()
    row5.add_channel_menu(
        f"{SUBDAY_CONFIG_PREFIX}staff_channel",
        channel_types=[hikari.ChannelType.GUILD_TEXT],
        placeholder="Staff channel (completions and milestones)",
        min_values=0,
        max_values=1,
    )
    defaults5 = (
        [{"id": str(channel_map[cfg.staff_channel]), "type": "channel"}]
        if cfg.staff_channel and cfg.staff_channel in channel_map
        else []
    )
    rows.append(_DefaultsActionRow(row5, defaults5))

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


MULTI_ROLE_FIELDS = {"enroll_role"}


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


def _resolve_multi_role_value(
    interaction: hikari.ComponentInteraction,
) -> list[str]:
    """Extract role names from a multi-select role interaction."""
    if (
        not interaction.values
        or not interaction.resolved
        or not interaction.resolved.roles
    ):
        return []
    names: list[str] = []
    for val in interaction.values:
        snowflake = hikari.Snowflake(val)
        role = interaction.resolved.roles.get(snowflake)
        if role:
            names.append(role.name)
    return names


def _display_config_value(v: object) -> str:
    """Format a config value for display in log/audit messages."""
    if isinstance(v, list):
        return ", ".join(v) if v else "None"
    return v or "None"  # type: ignore[return-value]


async def _reject_missing_perms(
    interaction: hikari.ComponentInteraction,
    bot: DragonpawBot,
    guild_id: hikari.Snowflakeish,
    guild_state: state.SubDayGuildState,
    channel_name: str,
) -> bool:
    """Check channel perms and send an error response if missing. Returns True if rejected."""
    channel_id = hikari.Snowflake(interaction.values[0])
    missing = await utils.check_channel_perms(bot, guild_id, channel_id)
    if not missing:
        return False
    missing_str = ", ".join(f"**{p}**" for p in missing)
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        content=(
            f"I can't use #{channel_name} — I'm missing these permissions: "
            f"{missing_str}. Please fix the channel permissions and try again."
        ),
        flags=hikari.MessageFlag.EPHEMERAL,
    )
    logger.warning(
        "G=%r U=%r: SubDay config rejected #%s — missing permissions: %s",
        guild_state.guild_name,
        interaction.user.username,
        channel_name,
        ", ".join(missing),
    )
    return True


async def handle_config_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle a component interaction from the config or prize-roles message."""
    custom_id = interaction.custom_id

    if custom_id.startswith(SUBDAY_CFG_ROLE_PREFIX):
        field = custom_id.removeprefix(SUBDAY_CFG_ROLE_PREFIX)
        embed_builder = _prize_roles_embed
        components_builder = _prize_roles_components
    elif custom_id.startswith(SUBDAY_CONFIG_PREFIX):
        field = custom_id.removeprefix(SUBDAY_CONFIG_PREFIX)
        embed_builder = _config_embed
        components_builder = _config_components
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

    # Only allow the guild owner
    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    guild = await bot.rest.fetch_guild(guild_id)
    if interaction.user.id != guild.owner_id:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="Only the server owner can change these settings.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    guild_state = state.load(int(guild_id))
    cfg = guild_state.config
    old_value = getattr(cfg, field)

    # Multi-role fields use a separate resolver
    if field in MULTI_ROLE_FIELDS:
        new_value = _resolve_multi_role_value(interaction)
    else:
        new_value = _resolve_select_value(interaction, field)

    # For channel fields, verify the bot can write to the selected channel
    if new_value and field in ("achievements_channel", "staff_channel"):
        if await _reject_missing_perms(
            interaction, bot, guild_id, guild_state, new_value
        ):
            return

    if new_value == old_value:
        logger.debug(
            "G=%r U=%r: SubDay setting unchanged: %s = %r",
            guild.name,
            interaction.user.username,
            field,
            new_value,
        )
        embed = embed_builder(cfg)
        components = await components_builder(bot, guild_id, cfg)
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_UPDATE,
            embed=embed,
            components=components,
        )
        return

    setattr(cfg, field, new_value)
    guild_state.guild_name = guild.name
    state.save(guild_state)

    display_old = _display_config_value(old_value)
    display_new = _display_config_value(new_value)
    logger.info(
        "G=%r U=%r: SubDay setting changed: %s = %r (was %r)",
        guild.name,
        interaction.user.username,
        field,
        display_new,
        display_old,
    )

    # Log to the guild's log channel if configured
    bot_state = bot.state(guild_id)
    if bot_state and bot_state.log_channel_id:
        try:
            await bot.rest.create_message(
                channel=bot_state.log_channel_id,
                content=(
                    f"**SubDay config changed** by {interaction.user.mention}: "
                    f"`{field}` changed from `{display_old}` to `{display_new}`"
                ),
            )
        except hikari.HTTPError:
            logger.warning(
                "G=%r: Failed to log config change to log channel",
                guild.name,
            )

    embed = embed_builder(cfg)
    embed.set_footer(text="Settings updated.")
    components = await components_builder(bot, guild_id, cfg)
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_UPDATE,
        embed=embed,
        components=components,
    )


class SubDayConfig(
    lightbulb.SlashCommand,
    name="config",
    description="Configure SubDay settings for this server (owner only)",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_GUILD)],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        embed = _config_embed(cfg)
        components = await _config_components(bot, ctx.guild_id, cfg)
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


async def _prize_roles_components(
    bot: DragonpawBot,
    guild_id: hikari.Snowflakeish,
    cfg: SubDayGuildConfig,
) -> list[hikari.api.ComponentBuilder]:
    """Build the action rows for the prize-roles message with current values pre-selected."""
    roles = await bot.rest.fetch_roles(guild_id)
    role_map = {r.name: r.id for r in roles}

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
        role_name = getattr(cfg, f"role_{week}")
        defaults = (
            [{"id": str(role_map[role_name]), "type": "role"}]
            if role_name and role_name in role_map
            else []
        )
        rows.append(_DefaultsActionRow(row, defaults))
    return rows


class SubDayPrizeRoles(
    lightbulb.SlashCommand,
    name="prize-roles",
    description="Configure milestone roles for this server (owner only)",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_GUILD)],
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        embed = _prize_roles_embed(cfg)
        components = await _prize_roles_components(bot, ctx.guild_id, cfg)
        await ctx.respond(
            embed=embed,
            components=components,
            flags=hikari.MessageFlag.EPHEMERAL,
        )


class SubDayPrizes(
    lightbulb.SlashCommand,
    name="prizes",
    description="Set milestone prize descriptions (owner only)",
    hooks=[lightbulb.prefab.has_permissions(hikari.Permissions.MANAGE_GUILD)],
):
    prize_13 = lightbulb.string("prize_13", "Prize for week 13 milestone", default=None)
    prize_26 = lightbulb.string("prize_26", "Prize for week 26 milestone", default=None)
    prize_39 = lightbulb.string("prize_39", "Prize for week 39 milestone", default=None)
    prize_52 = lightbulb.string(
        "prize_52", "Prize for week 52 graduation", default=None
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        bot = _get_bot(ctx)
        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config
        changed = False

        for field in ("prize_13", "prize_26", "prize_39", "prize_52"):
            value = getattr(self, field, None)
            if value is not None:
                old_value = getattr(cfg, field)
                setattr(cfg, field, value)
                changed = True
                logger.info(
                    "G=%r U=%r: SubDay %s: %s -> %s",
                    guild_state.guild_name,
                    ctx.user.username,
                    field,
                    old_value,
                    value,
                )

        if changed:
            guild = await bot.rest.fetch_guild(ctx.guild_id)
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
