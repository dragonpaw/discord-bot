from __future__ import annotations

import lightbulb

loader = lightbulb.Loader()

from dragonpaw_bot.plugins.intros import commands as _commands  # noqa: E402

intros_group = lightbulb.Group("intros", "Introductions channel tools")
_commands.register(intros_group)
loader.command(intros_group)

from dragonpaw_bot.plugins.intros import cron as _cron  # noqa: E402, F401
