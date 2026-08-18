import os

import pytest

# bot.py reads these env vars at import time, so set dummies before importing.
os.environ.setdefault(
    "BOT_TOKEN", "MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GabcDE.fake-token-for-tests-only"
)
os.environ.setdefault("CLIENT_ID", "000000000000000000")

import dragonpaw_bot.bot as bot_module
from dragonpaw_bot import journal


@pytest.fixture()
def state_dir(monkeypatch, tmp_path):
    """Monkeypatch STATE_DIR to a temporary directory."""
    monkeypatch.setattr(bot_module, "STATE_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_journal_store(monkeypatch, tmp_path):
    """Keep journal writes out of the repo's real state/ directory.

    gc.log() journals through the module-global store, so any test touching a
    plugin that logs an observed event writes through it — usually without
    knowing. Autouse because the leak is invisible at the call site.
    """
    monkeypatch.setattr(journal.store, "state_dir", tmp_path)
    journal.store.cache.clear()
    yield
    journal.store.cache.clear()
