"""Tests for conversation memory threading (memory_thread, v6.86.0)."""
import pytest


@pytest.fixture
def mt(load):
    return load("memory_thread")


def test_shape_filters_and_orders(mt):
    rows = [{"role": "user", "content": "hi"},
            {"role": "system", "content": "x"},          # non-conversational dropped
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "   "}]           # empty dropped
    assert mt.shape_history(rows, limit=10) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_shape_truncates_long(mt):
    out = mt.shape_history([{"role": "user", "content": "x" * 1000}], char_cap=50)
    assert len(out[0]["content"]) <= 51 and out[0]["content"].endswith("\u2026")


def test_shape_caps_to_last_n(mt):
    rows = [{"role": "user", "content": str(i)} for i in range(20)]
    out = mt.shape_history(rows, limit=5)
    assert len(out) == 5 and out[-1]["content"] == "19"   # keeps the most recent


def test_shape_handles_junk(mt):
    assert mt.shape_history(None) == []
    assert mt.shape_history([None, "x", 3]) == []          # non-dicts skipped


async def test_load_recent_reads_and_shapes(mt, fake_hass, load, monkeypatch):
    db = load("database")
    monkeypatch.setattr(db, "get_thread_messages",
                        lambda hours, limit, user_id, device_id: [
                            {"role": "user", "content": "earlier q"},
                            {"role": "assistant", "content": "earlier a"}])
    assert await mt.load_recent(fake_hass, 48, 12, "user-1") == [
        {"role": "user", "content": "earlier q"},
        {"role": "assistant", "content": "earlier a"},
    ]


async def test_load_recent_passes_the_persons_scope(mt, fake_hass, load, monkeypatch):
    seen = {}
    db = load("database")

    def _rec(hours, limit, user_id, device_id):
        seen.update(hours=hours, limit=limit, user_id=user_id, device_id=device_id)
        return []
    monkeypatch.setattr(db, "get_thread_messages", _rec)
    await mt.load_recent(fake_hass, 24, 7, "user-1", "tablet")
    # Clean Jarvis: one person, bounded; never the global thread
    assert seen == {"hours": 24, "limit": 7, "user_id": "user-1", "device_id": "tablet"}


def test_thread_messages_never_mix_people(load, tmp_path, monkeypatch):
    db = load("database")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "chat.db")
    db.save_message("user", "mine", device_id="tablet", user_id="user-1")
    db.save_message("user", "theirs", device_id="tablet", user_id="user-2")
    db.save_message("user", "no login", device_id="tablet", user_id="")
    assert [r["content"] for r in db.get_thread_messages(48, 10, "user-1", "tablet")] == ["mine"]
    assert [r["content"] for r in db.get_thread_messages(48, 10, "", "tablet")] == ["no login"]
    assert db.get_thread_messages(48, 10, "", "") == []       # nobody known: nothing, never everything


async def test_load_recent_db_error_empty(mt, fake_hass, load, monkeypatch):
    db = load("database")

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "get_thread_messages", boom)
    assert await mt.load_recent(fake_hass, 48, 12, "user-1") == []


def test_config_reads_jarvis_config(mt, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: {
        "memory_threading_enabled": False, "memory_threading_hours": 24,
        "memory_threading_max": 5}.get(k, d))
    assert mt.config() == (False, 24, 5)


def test_config_defaults(mt, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "get", lambda k, d=None: d)     # nothing set → defaults
    assert mt.config() == (mt.DEFAULT_ENABLED, mt.DEFAULT_HOURS, mt.DEFAULT_MAX)
