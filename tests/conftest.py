import os

import pytest

# bot.py reads these env vars at import time, so set dummies before importing.
os.environ.setdefault("BOT_TOKEN", "fake-token-for-tests")
os.environ.setdefault("CLIENT_ID", "000000000000000000")

import dragonpaw_bot.bot as bot_module  # noqa: E402


@pytest.fixture()
def state_dir(monkeypatch, tmp_path):
    """Monkeypatch STATE_DIR to a temporary directory."""
    monkeypatch.setattr(bot_module, "STATE_DIR", tmp_path)
    return tmp_path
