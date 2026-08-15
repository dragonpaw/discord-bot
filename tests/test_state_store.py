import pydantic
import pytest

from dragonpaw_bot.state_store import GuildStateBase, GuildStateStore


class _DemoState(GuildStateBase):
    counter: int = 0


@pytest.fixture
def store(tmp_path):
    s = GuildStateStore("demo", _DemoState)
    s.state_dir = tmp_path
    return s


def test_load_missing_file_returns_empty_state(store):
    st = store.load(100)
    assert st.guild_id == 100
    assert st.counter == 0


def test_save_load_round_trip(store):
    st = _DemoState(guild_id=200, guild_name="Guild", counter=3)
    store.save(st)
    store.cache.clear()
    loaded = store.load(200)
    assert loaded.guild_name == "Guild"
    assert loaded.counter == 3


def test_load_uses_cache(store):
    store.save(_DemoState(guild_id=300))
    assert store.load(300) is store.load(300)


def test_path_is_prefixed_per_store(store):
    assert store.path(5).name == "demo_5.yaml"


def test_load_empty_file_returns_empty_state(store):
    store.state_dir.mkdir(exist_ok=True)
    store.path(400).write_text("")
    st = store.load(400)
    assert st.guild_id == 400


def test_load_invalid_data_raises(store):
    store.state_dir.mkdir(exist_ok=True)
    store.path(500).write_text("guild_id: not-a-number\n")
    with pytest.raises(pydantic.ValidationError):
        store.load(500)
