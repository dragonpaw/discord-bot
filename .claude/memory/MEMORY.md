# Project Memory

## Git Workflow

- Push directly to `main` — no feature branches, no PRs.

## Feedback

- [Staff channel mentions](feedback_staff_channel_mentions.md) — Use Discord @mentions in staff/log channel messages, not bare usernames
- [Early returns](feedback_early_returns.md) — Use early returns to minimize conditional nesting
- [No re-exports, keep DRY](feedback_no_reexports_dry.md) — Never re-export through intermediate modules; import from source. Keep code DRY.
- [Config command permissions](feedback_config_permissions.md) — All `/config` subcommands use `owner_only` hook by default, not `MANAGE_GUILD`
- [Import before use (linter fires)](feedback_import_before_use.md) — Add using code before the import, or include both in one Edit; ruff strips unused imports between tool calls
- [Cache-first guild lookup](feedback_cache_first_guild_lookup.md) — Event listeners must use `cache.get_guild(id) or await rest.fetch_guild(id)`, never bare REST
- [Per-guild cron isolation](feedback_cron_per_guild_isolation.md) — Wrap each guild's cron body in try/except so one failure doesn't abort all guilds
- [State cleanup before side effects](feedback_state_before_side_effects.md) — Save state before outbound messages/API calls; wrap notifications in try/except
