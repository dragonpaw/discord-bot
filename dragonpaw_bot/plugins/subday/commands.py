from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import hikari
import lightbulb
import structlog

from dragonpaw_bot import utils
from dragonpaw_bot.colors import (
    SOLARIZED_CYAN,
    SOLARIZED_MAGENTA,
    SOLARIZED_VIOLET,
    SOLARIZED_YELLOW,
)
from dragonpaw_bot.context import GuildContext
from dragonpaw_bot.plugins.subday import chart, prompts, state
from dragonpaw_bot.plugins.subday.constants import (
    MAX_EMBEDS_PER_MESSAGE,
    MILESTONE_WEEKS,
    SUBDAY_OWNER_REQUEST_PREFIX,
    SUBDAY_SIGNUP_ID,
    TOTAL_WEEKS,
)
from dragonpaw_bot.plugins.subday.models import SubDayGuildConfig, SubDayParticipant

if TYPE_CHECKING:
    from dragonpaw_bot.bot import DragonpawBot

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------- #
#                                   Helpers                                    #
# ---------------------------------------------------------------------------- #


async def help_handler(ctx: lightbulb.Context) -> None:
    """Show contextual help listing commands the user can access."""
    assert ctx.guild_id
    gc = GuildContext.from_ctx(ctx)
    guild_state = state.load(int(ctx.guild_id))
    cfg = guild_state.config

    lines = [
        "`/subday about` — Learn about the program",
        "`/subday status` — Check your progress (and your subs')",
        "`/subday owner` — Set or clear your owner",
    ]
    if gc.member:
        if gc.has_any_permission(cfg.enroll_role):
            lines.append("`/subday signup` — Sign up for the program")
        if gc.has_permission(cfg.complete_role):
            lines.append("`/subday complete @user` — Mark a week complete")
            lines.append("`/subday list` — View all participants")
            lines.append("`/subday remove @user` — Remove a participant")
        if gc.has_permission(cfg.backfill_role):
            lines.append(
                "`/subday complete @user week:<n>` — Backfill to a specific week"
            )
    guild = await gc.fetch_guild()
    if ctx.user.id == guild.owner_id:
        lines.append("`/config subday settings` — Configure settings (server owner)")
        lines.append(
            "`/config subday prize-roles` — Set milestone roles (server owner)"
        )
        lines.append("`/config subday prizes` — Set milestone prizes (server owner)")

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
    guild_name: str,
) -> None:
    """DM the target their completion embed and star chart."""
    try:
        dm = await target.user.fetch_dm_channel()
        embed = _regular_completion_embed(target, week)
        embed.set_image(chart_bytes)
        await dm.send(embed=embed)
        logger.info(
            "Sent completion DM",
            guild=guild_name,
            user=target.username,
            week=week,
        )
    except hikari.HTTPError as exc:
        logger.warning(
            "Cannot DM user for completion notice",
            guild=guild_name,
            user=target.username,
            error=str(exc),
        )


def _milestone_prize_teaser(current_week: int, cfg: SubDayGuildConfig) -> str:
    """Return a prize-teaser string for the next upcoming milestone, or ''."""
    next_milestone = next((m for m in MILESTONE_WEEKS if m >= current_week), None)
    if not next_milestone:
        return ""
    prize = cfg.milestone_prizes().get(next_milestone, "a prize")
    weeks_away = next_milestone - current_week
    if weeks_away == 0:
        return f"\n🎁 Milestone week! Prize: **{prize}**"
    return f"\n🎁 Next prize ({weeks_away}w away): **{prize}**"


async def _try_post_achievement_embed(
    achievements: hikari.GuildTextChannel | None,
    embed: hikari.Embed,
    chart_bytes: hikari.Bytes,
    channel_name: str | None,
) -> None:
    if not achievements:
        return
    try:
        embed.set_image(chart_bytes)
        await achievements.send(embed=embed)
    except hikari.HTTPError as exc:
        logger.warning(
            "Failed to post achievement", channel=channel_name, error=str(exc)
        )


async def _post_achievement(
    gc: GuildContext,
    completer: hikari.Member,
    target: hikari.Member,
    week: int,
    cfg: SubDayGuildConfig,
) -> None:
    """Post achievement embed and handle milestone/graduation rewards."""
    logger.debug(
        "Posting achievement",
        guild=gc.name,
        user=target.username,
        week=week,
    )
    prizes = cfg.milestone_prizes()

    # Fetch channels once upfront
    channels = await gc.bot.rest.fetch_guild_channels(gc.guild_id)
    channel_map = {
        c.name: c for c in channels if isinstance(c, hikari.GuildTextChannel)
    }

    achievements = None
    if cfg.achievements_channel:
        achievements = channel_map.get(cfg.achievements_channel)
        if not achievements:
            logger.warning(
                "Achievements channel not found, embed will not be posted",
                channel=cfg.achievements_channel,
            )

    # Generate star chart attachment (use guild display name)
    chart_bytes = chart.render_star_chart(
        username=target.display_name,
        current_week=week,
        week_completed=True,
    )

    await _dm_completion(target, week, chart_bytes, gc.name)

    milestone_roles = cfg.milestone_roles()

    # Regular completion — just post the embed
    if week not in milestone_roles:
        embed = _regular_completion_embed(target, week)
        await _try_post_achievement_embed(
            achievements, embed, chart_bytes, cfg.achievements_channel
        )
        return

    role_name = milestone_roles[week]

    # Milestone or graduation
    if week == TOTAL_WEEKS:
        logger.info(
            "GRADUATED from Where I am Led!",
            guild=gc.name,
            user=target.username,
        )
        embed = _graduation_embed(target, prizes)
        staff_msg = (
            f"🎓 {completer.mention} — {target.mention} has **graduated** "
            f"from Where I am Led! Please arrange their prize: "
            f"**{prizes.get(TOTAL_WEEKS, 'a prize')}**."
        )
    else:
        logger.info(
            "Reached milestone week",
            guild=gc.name,
            user=target.username,
            week=week,
            role=role_name or "no role",
        )
        embed = _milestone_embed(target, week, role_name, prizes)
        prize = prizes.get(week, "a prize")
        staff_msg = (
            f"🎁 {completer.mention} — {target.mention} has reached the "
            f"**week {week} milestone** of Where I am Led! "
            f"Please arrange their prize: **{prize}**."
        )

    await _try_post_achievement_embed(
        achievements, embed, chart_bytes, cfg.achievements_channel
    )

    # Assign milestone role (skip if None)
    if role_name:
        role = await utils.guild_role_by_name(gc, role_name)
        if role:
            await target.add_role(role, reason=f"SubDay: week {week}")
            logger.info(
                "Assigned milestone role",
                guild=gc.name,
                user=target.username,
                role=role.name,
            )
        else:
            logger.warning("Milestone role not found in guild", role=role_name)

    # Log to guild log channel
    await gc.log(staff_msg)


# ---------------------------------------------------------------------------- #
#                              Shared signup logic                             #
# ---------------------------------------------------------------------------- #


def _do_signup(
    bot: DragonpawBot,
    guild_id: hikari.Snowflake,
    user: hikari.User,
) -> str | None:
    """Register a user for SubDay. Returns None if already enrolled, or a
    response message. Caller must respond first, then call _do_signup_async().
    """
    guild_state = state.load(int(guild_id))
    user_id = int(user.id)

    if user_id in guild_state.participants:
        logger.debug(
            "SubDay signup rejected, already enrolled",
            guild=guild_state.guild_name,
            user=user.username,
        )
        return None

    participant = SubDayParticipant(
        user_id=user_id,
        signup_date=datetime.datetime.now(tz=datetime.UTC),
    )
    guild_state.participants[user_id] = participant
    state.save(guild_state)

    return (
        "🐉 Rawr, you're in! You've been signed up for **Where I am Led**! "
        "Check your DMs for your first prompt~ 💜"
    )


async def _do_signup_async(
    bot: DragonpawBot,
    guild_id: hikari.Snowflake,
    user: hikari.User,
) -> None:
    """Send welcome DM and log to guild. Call after responding to the interaction."""
    guild_state = state.load(int(guild_id))
    user_id = int(user.id)
    participant = guild_state.participants.get(user_id)
    if not participant:
        return

    guild = await bot.rest.fetch_guild(guild_id)
    guild_state.guild_name = guild.name
    gc = GuildContext.from_guild(bot, guild)

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
    except hikari.HTTPError as exc:
        logger.warning(
            "Cannot DM user for SubDay signup",
            guild=guild.name,
            user=user.username,
            error=str(exc),
        )
    logger.info(
        "Signed up for SubDay",
        guild=guild.name,
        user=user.username,
    )
    await gc.log(f"📝 {user.mention} signed up for **Where I am Led**.")


async def handle_signup_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle the Sign Up button click from /subday about."""
    guild_id = interaction.guild_id
    if not guild_id or not interaction.member:
        logger.warning(
            "Signup interaction missing guild_id or member",
            guild_id=interaction.guild_id,
            has_member=interaction.member is not None,
        )
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*sad dragon noises* 🐉 Something went wrong with your signup. Please try again!",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    bot: DragonpawBot = interaction.app  # type: ignore[assignment]

    guild_state = state.load(int(guild_id))
    cfg = guild_state.config

    gc = GuildContext.from_interaction(interaction)
    if not gc.has_any_permission(cfg.enroll_role):
        label = (
            "one of the **" + "**, **".join(cfg.enroll_role) + "** roles"
            if cfg.enroll_role
            else "server owner status"
        )
        logger.warning(
            "SubDay signup denied, missing permission",
            required=label,
        )
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content=f"*little puff of smoke* 🐉 You need {label} to sign up!",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    msg = _do_signup(bot, guild_id, interaction.user)
    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        content=msg
        or "You're already signed up, silly! 🐉 Use `/subday status` to check your progress~",
        flags=hikari.MessageFlag.EPHEMERAL,
    )
    if msg:
        await _do_signup_async(bot, guild_id, interaction.user)


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


class SubDayAbout(
    lightbulb.SlashCommand,
    name="about",
    description="Learn about the Where I am Led program",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        guild_state = state.load(int(ctx.guild_id))
        log = logger.bind(guild=guild_state.guild_name, user=ctx.user.username)
        log.info("Viewed SubDay about")
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

        gc = GuildContext.from_ctx(ctx)
        row = gc.bot.rest.build_message_action_row()
        row.add_interactive_button(
            hikari.ButtonStyle.SUCCESS, SUBDAY_SIGNUP_ID, label="Sign Up"
        )
        await ctx.respond(
            embeds=[intro, details, signup],
            component=row,
            flags=hikari.MessageFlag.EPHEMERAL,
        )


def _owned_sub_status_embed(
    sub_user_id: int, p: SubDayParticipant, cfg: SubDayGuildConfig, sub_name: str
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

    status += _milestone_prize_teaser(p.current_week, cfg)
    status += (
        f"\n\n**Progress**: {p.current_week}/{TOTAL_WEEKS} weeks"
        f"\n**Signed up**: <t:{int(p.signup_date.timestamp())}:R>"
    )

    return hikari.Embed(
        title=f"{icon} @{sub_name}'s Progress",
        description=status,
        color=SOLARIZED_CYAN,
    )


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

    next_milestone = next((m for m in MILESTONE_WEEKS if m >= p.current_week), None)
    if next_milestone:
        milestone_roles = cfg.milestone_roles()
        role_name = milestone_roles.get(next_milestone)
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

    status_text += _milestone_prize_teaser(p.current_week, cfg)

    chart_bytes = chart.render_star_chart(
        username=display_name,
        current_week=p.current_week,
        week_completed=p.week_completed,
    )

    status_text += (
        f"\n\n**Progress**: {p.current_week}/{TOTAL_WEEKS} weeks"
        f"\n**Signed up**: <t:{int(p.signup_date.timestamp())}:R>"
    )
    if p.owner_id:
        status_text += f"\n**👤 Owner**: <@{p.owner_id}>"

    embed = hikari.Embed(
        title="Where I am Led — Your Progress",
        description=status_text,
        color=SOLARIZED_VIOLET,
    )
    embed.set_image(chart_bytes)
    return embed


class SubDayStatus(
    lightbulb.SlashCommand,
    name="status",
    description="Check your own progress in Where I am Led",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id
        guild_state = state.load(int(ctx.guild_id))
        log = logger.bind(guild=guild_state.guild_name, user=ctx.user.username)
        log.info("Checking SubDay status")
        user_id = int(ctx.user.id)

        own_participant = guild_state.participants.get(user_id)
        owned_subs = [
            (uid, p)
            for uid, p in guild_state.participants.items()
            if p.owner_id == user_id
        ]

        if not own_participant and not owned_subs:
            await ctx.respond(
                "🐉 You're not signed up for Where I am Led yet! "
                "Use `/subday signup` to get started~",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        cfg = guild_state.config
        embeds: list[hikari.Embed] = []

        if own_participant:
            display_name = ctx.member.display_name if ctx.member else ctx.user.username
            embeds.append(_own_progress_embed(own_participant, display_name, cfg))

        gc = GuildContext.from_ctx(ctx)
        for sub_uid, sub_p in owned_subs:
            if len(embeds) >= MAX_EMBEDS_PER_MESSAGE:
                break
            try:
                member = await gc.bot.rest.fetch_member(ctx.guild_id, sub_uid)
                sub_name = member.display_name
            except hikari.NotFoundError:
                sub_name = f"User {sub_uid}"
            embeds.append(_owned_sub_status_embed(sub_uid, sub_p, cfg, sub_name))

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
        gc = GuildContext.from_ctx(ctx)
        guild_state = state.load(int(ctx.guild_id))
        log = gc.logger
        user_id = int(ctx.user.id)

        if user_id not in guild_state.participants:
            await ctx.respond(
                "🐉 You're not signed up for Where I am Led yet! "
                "Use `/subday signup` to get started~",
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
                log.info("Cleared owner")
            await ctx.respond(
                "🐉 Got it! Your owner has been cleared 🐾",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        target_id = int(target.id)

        # Can't set self
        if target_id == user_id:
            await ctx.respond(
                "*bonks you gently* 🐉 You can't be your own owner, silly!",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        # Already confirmed owner
        if participant.owner_id == target_id:
            await ctx.respond(
                f"🐉 {target.mention} is already your owner! You're all set~ 🐾",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        # Target must be a member of this guild
        try:
            await gc.bot.rest.fetch_member(ctx.guild_id, target_id)
        except hikari.NotFoundError:
            await ctx.respond(
                f"🐉 {target.mention} doesn't seem to be in this server!",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        # Send approval DM to the target
        participant.pending_owner_id = target_id

        guild = await gc.fetch_guild()
        row = gc.bot.rest.build_message_action_row()
        row.add_interactive_button(
            hikari.ButtonStyle.SUCCESS,
            f"{SUBDAY_OWNER_REQUEST_PREFIX}approve:{ctx.guild_id}:{user_id}",
            label="Accept",
        )
        row.add_interactive_button(
            hikari.ButtonStyle.DANGER,
            f"{SUBDAY_OWNER_REQUEST_PREFIX}deny:{ctx.guild_id}:{user_id}",
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
            log.warning(
                "Failed to DM owner request",
                target=target.username,
                error=str(exc),
            )
            await ctx.respond(
                f"*sad puff* 🐉 I couldn't send a DM to {target.mention} — "
                "they might have DMs disabled. The request wasn't sent!",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        state.save(guild_state)
        log.info("Sent owner request", target=target.username)
        await ctx.respond(
            f"🐉 Done! I've sent an owner request to {target.mention}. "
            "They'll need to accept it in their DMs~ 💜",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


async def _notify_sub_of_owner_decision(  # noqa: PLR0913
    bot: DragonpawBot,
    guild_name: str,
    guild_id: int,
    sub_user_id: int,
    owner_user_id: int,
    approved: bool,
) -> None:
    dm_text = (
        f"*excited bouncing* 🐉 <@{owner_user_id}> has **accepted** your owner request! "
        "They'll get copies of your weekly prompts now~ 💜"
        if approved
        else f"🐉 <@{owner_user_id}> has declined your owner request. That's okay! 🐾"
    )
    log_text = (
        f"🤝 **SubDay owner accepted** — <@{owner_user_id}> accepted ownership of <@{sub_user_id}>"
        if approved
        else f"😢 **SubDay owner declined** — <@{owner_user_id}> declined ownership of <@{sub_user_id}>"
    )
    try:
        sub_user = await bot.rest.fetch_user(hikari.Snowflake(sub_user_id))
        dm = await sub_user.fetch_dm_channel()
        await dm.send(dm_text)
    except hikari.HTTPError:
        logger.warning(
            "Could not DM sub about owner decision",
            guild=guild_name,
            sub_id=sub_user_id,
            approved=approved,
        )
    # Build a minimal GuildContext for logging
    guild = bot.cache.get_guild(hikari.Snowflake(guild_id))
    if guild:
        gc = GuildContext.from_guild(bot, guild)
    else:
        state_obj = bot.state(hikari.Snowflake(guild_id))
        gc = GuildContext(
            bot=bot,
            guild_id=hikari.Snowflake(guild_id),
            name=guild_name,
            log_channel_id=state_obj.log_channel_id if state_obj else None,
        )
    await gc.log(log_text)


async def _handle_owner_approve(
    interaction: hikari.ComponentInteraction,
    guild_state: state.SubDayGuildState,
    participant: SubDayParticipant,
    guild_id: int,
    sub_user_id: int,
) -> None:
    """Process an owner approval interaction."""
    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    owner_user_id = int(interaction.user.id)

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
            content="🐉 It looks like you're no longer in that server, so the request has been cancelled.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    participant.owner_id = owner_user_id
    participant.pending_owner_id = None
    state.save(guild_state)

    logger.info(
        "Owner approved",
        guild=guild_state.guild_name,
        owner_id=owner_user_id,
        sub_id=sub_user_id,
    )

    # Build a role mention for the complete role
    cfg = guild_state.config
    complete_mention = "a server admin"
    if cfg.complete_role:
        role = await utils.guild_role_by_name(
            GuildContext.from_guild(
                bot,
                bot.cache.get_guild(hikari.Snowflake(guild_id))
                or await bot.rest.fetch_guild(hikari.Snowflake(guild_id)),
            ),
            cfg.complete_role,
        )
        complete_mention = role.mention if role else f"**{cfg.complete_role}**"

    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        content=(
            f"🐉 You've accepted! You're now the owner for <@{sub_user_id}>~ 💜\n\n"
            f"Here's what that means:\n"
            f"• Every **Sunday**, you'll get a copy of their weekly prompt so you can follow along 📖\n"
            f"• Every **Friday**, I'll nudge you if they haven't finished their current week yet 🔔\n"
            f"• To get their weeks approved, reach out to someone with the {complete_mention} role~ 🐾"
        ),
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    await _notify_sub_of_owner_decision(
        bot, guild_state.guild_name, guild_id, sub_user_id, owner_user_id, approved=True
    )


async def _handle_owner_deny(
    interaction: hikari.ComponentInteraction,
    guild_state: state.SubDayGuildState,
    participant: SubDayParticipant,
    guild_id: int,
    sub_user_id: int,
) -> None:
    """Process an owner denial interaction."""
    bot: DragonpawBot = interaction.app  # type: ignore[assignment]
    owner_user_id = int(interaction.user.id)

    participant.pending_owner_id = None
    state.save(guild_state)

    logger.info(
        "Owner denied",
        guild=guild_state.guild_name,
        owner_id=owner_user_id,
        sub_id=sub_user_id,
    )

    await interaction.create_initial_response(
        response_type=hikari.ResponseType.MESSAGE_CREATE,
        content="🐉 Understood! The request has been declined.",
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    await _notify_sub_of_owner_decision(
        bot,
        guild_state.guild_name,
        guild_id,
        sub_user_id,
        owner_user_id,
        approved=False,
    )


async def handle_owner_interaction(interaction: hikari.ComponentInteraction) -> None:
    """Handle Accept/Decline button clicks for owner requests."""
    cid = interaction.custom_id
    owner_user_id = int(interaction.user.id)

    # Format: subday_owner_request:approve|deny:guild_id:sub_user_id
    _OWNER_REQUEST_PARTS = 3
    parts = cid.removeprefix(SUBDAY_OWNER_REQUEST_PREFIX).split(":")
    if len(parts) != _OWNER_REQUEST_PARTS or parts[0] not in ("approve", "deny"):
        logger.warning("Unrecognized owner interaction custom_id", custom_id=cid)
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="*confused head tilt* 🐉 This button doesn't seem to work anymore!",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    approve = parts[0] == "approve"
    guild_id = int(parts[1])
    sub_user_id = int(parts[2])

    guild_state = state.load(guild_id)
    participant = guild_state.participants.get(sub_user_id)

    if not participant:
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="🐉 This person isn't in the program anymore!",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # Check if this request is still valid
    if participant.pending_owner_id != owner_user_id:
        # Double-click case: already approved
        if participant.owner_id == owner_user_id:
            await interaction.create_initial_response(
                response_type=hikari.ResponseType.MESSAGE_CREATE,
                content="🐉 You're already their owner! All good~ 🐾",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return
        await interaction.create_initial_response(
            response_type=hikari.ResponseType.MESSAGE_CREATE,
            content="🐉 This request isn't valid anymore — they may have changed their mind.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    if approve:
        await _handle_owner_approve(
            interaction, guild_state, participant, guild_id, sub_user_id
        )
    else:
        await _handle_owner_deny(
            interaction, guild_state, participant, guild_id, sub_user_id
        )


class SubDaySignup(
    lightbulb.SlashCommand,
    name="signup",
    description="Sign up for the Where I am Led program",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member
        gc = GuildContext.from_ctx(ctx)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await gc.check_permission(ctx, cfg.enroll_role, "signup"):
            return

        msg = _do_signup(gc.bot, ctx.guild_id, ctx.user)
        if msg is None:
            await ctx.respond(
                "You're already signed up, silly! 🐉 Use `/subday status` to check your progress~",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return
        await ctx.respond(msg, flags=hikari.MessageFlag.EPHEMERAL)
        await _do_signup_async(gc.bot, ctx.guild_id, ctx.user)


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
        return f"🐉 {target.mention} isn't signed up for Where I am Led!"

    participant = guild_state.participants[target_id]

    if participant.week_completed:
        return (
            f"🐉 {target.mention} already finished week "
            f"{participant.current_week}! "
            "They'll get their next prompt on Sunday~"
        )

    if participant.current_week > TOTAL_WEEKS:
        return f"🎓 {target.mention} has already graduated! 🐉✨"

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
    sent = lightbulb.boolean(
        "sent",
        "Backfill only: mark the week's prompt as already sent",
        default=None,
    )

    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:  # noqa: PLR0912, PLR0915
        assert ctx.guild_id and ctx.member
        gc = GuildContext.from_ctx(ctx)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        target: hikari.Member = self.user  # type: ignore[assignment]
        target_id = int(target.id)

        requested_week: int | None = self.week
        has_explicit_week = requested_week is not None
        is_backfill = has_explicit_week

        required_role = cfg.backfill_role if is_backfill else cfg.complete_role
        action = "backfill" if is_backfill else "complete"

        if not gc.has_permission(required_role):
            gc.logger.warning(
                "Command denied, missing permission",
                action=action,
                required=required_role,
            )
            role_mention = f"**{required_role}**" if required_role else None
            if required_role:
                role_obj = await utils.guild_role_by_name(gc, required_role)
                if role_obj:
                    role_mention = role_obj.mention
            if role_mention:
                await ctx.respond(
                    f"*little puff of smoke* 🐉 You need to ask someone with the {role_mention} role "
                    f"to review and approve the week for you~ 🐾",
                    flags=hikari.MessageFlag.EPHEMERAL,
                )
            else:
                await ctx.respond(
                    "*little puff of smoke* 🐉 Only the server owner can do that!",
                    flags=hikari.MessageFlag.EPHEMERAL,
                )
            return

        # Prevent self-completion
        if target.id == ctx.user.id:
            logger.debug("SubDay self-completion blocked", user=ctx.user.username)
            complete_mention = f"**{cfg.complete_role}**" if cfg.complete_role else None
            if cfg.complete_role:
                role_obj = await utils.guild_role_by_name(gc, cfg.complete_role)
                if role_obj:
                    complete_mention = role_obj.mention
            if complete_mention:
                await ctx.respond(
                    f"*boops your snoot* 🐉 Silly, you can't approve your own work! "
                    f"Find someone with the {complete_mention} role to check it~ 🐾",
                    flags=hikari.MessageFlag.EPHEMERAL,
                )
            else:
                await ctx.respond(
                    "*boops your snoot* 🐉 Silly, you can't approve your own work! "
                    "Ask the server owner to check it~ 🐾",
                    flags=hikari.MessageFlag.EPHEMERAL,
                )
            return

        if is_backfill:
            participant, auto_enrolled = _prepare_backfill(
                guild_state, target_id, requested_week
            )  # type: ignore[arg-type]
        else:
            error = _validate_normal_complete(guild_state, target, target_id)
            if error:
                await ctx.respond(error, flags=hikari.MessageFlag.EPHEMERAL)
                return
            participant = guild_state.participants[target_id]
            auto_enrolled = False

        week = participant.current_week
        participant.week_completed = True
        participant.reminder_sent = False
        participant.last_completed_date = datetime.datetime.now(tz=datetime.UTC)

        sent_next = bool(
            self.sent and has_explicit_week and participant.current_week < TOTAL_WEEKS
        )
        if sent_next:
            participant.current_week += 1
            participant.week_completed = False
            participant.week_sent = True

        state.save(guild_state)

        # Respond first to avoid interaction timeout, then do async work
        if auto_enrolled:
            response = (
                f"Enrolled {target.mention} and completed "
                f"**Week {week}** of Where I am Led."
            )
        elif sent_next:
            response = (
                f"Marked {target.mention} as complete for **Week {week}**, "
                f"advanced to Week {week + 1} (prompt already sent)."
            )
        else:
            response = f"Marked {target.mention} as complete for **Week {week}**."

        await ctx.respond(response, flags=hikari.MessageFlag.EPHEMERAL)

        await _post_achievement(gc, ctx.member, target, week, cfg)

        # Log completion (milestones are already logged by _post_achievement,
        # so only send for regular completions/backfills)
        milestone_roles = cfg.milestone_roles()
        if week not in milestone_roles:
            if is_backfill:
                staff_msg = (
                    f"⏩ {ctx.member.mention} backfilled {target.mention} "
                    f"to **Week {week}** (complete)"
                    + (
                        f", advanced to Week {week + 1} (prompt already sent)"
                        if sent_next
                        else ""
                    )
                    + "."
                )
            elif sent_next:
                staff_msg = (
                    f"✅ {ctx.member.mention} marked {target.mention} "
                    f"complete for **Week {week}**, "
                    f"and advanced to Week {week + 1} (prompt already sent)."
                )
            else:
                staff_msg = (
                    f"✅ {ctx.member.mention} marked {target.mention} "
                    f"complete for **Week {week}**."
                )
            await gc.log(staff_msg)
        logger.info(
            "Completed SubDay week",
            guild=gc.name,
            user=target.username,
            week=week,
            marked_by=ctx.user.username,
            backfill=is_backfill,
        )

        # Notify owner that their sub completed a week
        if participant.owner_id:
            try:
                owner_user = await gc.bot.rest.fetch_user(
                    hikari.Snowflake(participant.owner_id)
                )
                dm = await owner_user.fetch_dm_channel()
                await dm.send(
                    f"*happy tail wag* 🐉✨ <@{target_id}> just finished "
                    f"**Week {week}** of Where I am Led! "
                    f"They did a great job and deserve some praise~ 💜"
                )
                logger.info(
                    "Notified owner of completion",
                    owner_id=participant.owner_id,
                    sub_id=target_id,
                    week=week,
                )
            except hikari.ForbiddenError:
                logger.warning(
                    "Cannot DM owner about completion (DMs disabled)",
                    owner_id=participant.owner_id,
                )
            except hikari.HTTPError as exc:
                logger.warning(
                    "Failed to DM owner about completion",
                    owner_id=participant.owner_id,
                    error=str(exc),
                )


class SubDayList(
    lightbulb.SlashCommand,
    name="list",
    description="List all participants and their progress",
):
    @lightbulb.invoke
    async def invoke(self, ctx: lightbulb.Context) -> None:
        assert ctx.guild_id and ctx.member
        gc = GuildContext.from_ctx(ctx)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await gc.check_permission(ctx, cfg.complete_role, "list"):
            return

        gc.logger.info(
            "Listing SubDay participants", enrolled=len(guild_state.participants)
        )

        if not guild_state.participants:
            await ctx.respond(
                "🐉 No one has signed up for Where I am Led yet!",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        # Fetch display names for sorting
        display_names: dict[int, str] = {}
        for uid in guild_state.participants:
            try:
                member = await gc.bot.rest.fetch_member(
                    gc.guild_id, hikari.Snowflake(uid)
                )
                display_names[uid] = member.display_name.lower()
            except hikari.NotFoundError:
                display_names[uid] = ""

        lines: list[str] = []
        for uid, p in sorted(
            guild_state.participants.items(),
            key=lambda x: (x[1].current_week, display_names.get(x[0], "")),
        ):
            icon = "✅" if p.week_completed else "⏳"
            if p.current_week > TOTAL_WEEKS:
                icon = "🎓"
            line = f"{icon} <@{uid}> — Week {p.current_week}/{TOTAL_WEEKS}"
            if p.owner_id:
                line += f" (owner: <@{p.owner_id}>)"
            lines.append(line)

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
        gc = GuildContext.from_ctx(ctx)

        guild_state = state.load(int(ctx.guild_id))
        cfg = guild_state.config

        if not await gc.check_permission(ctx, cfg.complete_role, "remove"):
            return

        target: hikari.Member = self.user  # type: ignore[assignment]
        target_id = int(target.id)

        if target_id not in guild_state.participants:
            await ctx.respond(
                f"🐉 {target.mention} isn't signed up for Where I am Led!",
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
            f"🐉 {target.mention} has been removed from Where I am Led 🐾",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        logger.info(
            "Removed from SubDay",
            guild=guild_state.guild_name,
            user=target.username,
            removed_by=ctx.user.username,
        )
        await gc.log(
            f"🗑️ {ctx.member.mention} removed {target.mention} from **Where I am Led**.",
        )
