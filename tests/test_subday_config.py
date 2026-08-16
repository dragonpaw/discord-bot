from unittest.mock import AsyncMock, MagicMock

import hikari

from dragonpaw_bot.plugins.subday import config as subday_config
from dragonpaw_bot.plugins.subday import state


def _config_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.custom_id = "subday_cfg:enroll_role"
    interaction.guild_id = hikari.Snowflake(1)
    interaction.member.permissions = hikari.Permissions.MANAGE_GUILD
    interaction.values = []
    interaction.resolved = None
    interaction.create_initial_response = AsyncMock()
    interaction.edit_initial_response = AsyncMock()
    interaction.app.rest.fetch_guild = AsyncMock()
    return interaction


async def test_config_interaction_defers_before_rest_work(tmp_path, monkeypatch):
    """Component interactions get no auto-defer — the handler must defer
    before any REST call (3-second deadline)."""
    monkeypatch.setattr(state.store, "state_dir", tmp_path)
    state.store.cache.clear()
    monkeypatch.setattr(subday_config, "_config_components", AsyncMock(return_value=[]))
    interaction = _config_interaction()

    await subday_config.handle_config_interaction(interaction)

    response_type = interaction.create_initial_response.call_args.kwargs[
        "response_type"
    ]
    assert response_type == hikari.ResponseType.DEFERRED_MESSAGE_UPDATE
    interaction.app.rest.fetch_guild.assert_not_awaited()
