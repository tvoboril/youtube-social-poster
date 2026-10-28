import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import pytest

from app import main
from app.config import settings
from app.store import PostedStore

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>yt:video:VIDEO123</id>
    <yt:videoId>VIDEO123</yt:videoId>
    <yt:channelId>{channel}</yt:channelId>
    <title>Sunday Divine Liturgy</title>
  </entry>
</feed>""".format(channel=settings.yt_channel_id).encode()


def _sign(body: bytes) -> str:
    return "sha1=" + hmac.new(settings.websub_secret.encode(), body, hashlib.sha1).hexdigest()


def test_signature_valid_and_invalid():
    assert main._valid_signature(_sign(ATOM), ATOM) is True
    assert main._valid_signature("sha1=deadbeef", ATOM) is False
    assert main._valid_signature("", ATOM) is False


def test_parse_notification():
    video_id, channel_id = main._parse_notification(ATOM)
    assert video_id == "VIDEO123"
    assert channel_id == settings.yt_channel_id


def test_parse_notification_garbage():
    assert main._parse_notification(b"not xml") == (None, None)


def test_render_message(monkeypatch):
    monkeypatch.setattr(settings, "post_template", "{title}\\n\\n{description}")
    monkeypatch.setattr(settings, "post_description", "Watch live.")
    assert settings.render_message("Divine Liturgy") == "Divine Liturgy\n\nWatch live."


def test_render_message_empty_description(monkeypatch):
    monkeypatch.setattr(settings, "post_template", "{title}\\n\\n{description}")
    monkeypatch.setattr(settings, "post_description", "")
    # Trailing blank lines from an empty description are stripped.
    assert settings.render_message("Divine Liturgy") == "Divine Liturgy"


def test_store_dedupe(tmp_path):
    store = PostedStore(str(tmp_path / "db.sqlite3"))
    assert store.already_posted("abc") is False
    store.mark_posted("abc")
    assert store.already_posted("abc") is True
    store.mark_posted("abc")  # idempotent
    assert store.already_posted("abc") is True


@pytest.mark.asyncio
async def test_process_video_posts_when_live(tmp_path, monkeypatch):
    store = PostedStore(str(tmp_path / "db.sqlite3"))
    monkeypatch.setattr(main, "store", store)

    posted = []

    async def fake_info(vid):
        return "live", "Sunday Divine Liturgy", None

    async def fake_post(vid, title):
        posted.append((vid, title))
        return "post_1"

    monkeypatch.setattr(main, "get_video_info", fake_info)
    monkeypatch.setattr(main, "post_live_video", fake_post)

    await main.process_video("VIDEO123")
    assert posted == [("VIDEO123", "Sunday Divine Liturgy")]
    assert store.already_posted("VIDEO123")

    # Second push for same video is a no-op (dedupe).
    await main.process_video("VIDEO123")
    assert posted == [("VIDEO123", "Sunday Divine Liturgy")]


@pytest.mark.asyncio
async def test_process_video_skips_when_not_live(tmp_path, monkeypatch):
    store = PostedStore(str(tmp_path / "db.sqlite3"))
    monkeypatch.setattr(main, "store", store)

    posted = []

    async def fake_info(vid):
        return "none", None, None

    async def fake_post(vid, title):
        posted.append(vid)

    monkeypatch.setattr(main, "get_video_info", fake_info)
    monkeypatch.setattr(main, "post_live_video", fake_post)

    await main.process_video("REGULARVID")
    assert posted == []
    assert store.already_posted("REGULARVID") is False


def test_store_pending(tmp_path):
    store = PostedStore(str(tmp_path / "db.sqlite3"))
    assert store.get_pending("abc") is None
    store.set_pending("abc", 100)
    store.set_pending("abc", 200)  # schedule changed
    assert store.get_pending("abc") == 200
    assert store.all_pending() == [("abc", 200)]
    store.clear_pending("abc")
    assert store.all_pending() == []


class FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, func, trigger, run_date, args, id, **kwargs):
        self.jobs[id] = (run_date, args)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated store + fake scheduler + recorded Facebook posts."""
    store = PostedStore(str(tmp_path / "db.sqlite3"))
    sched = FakeScheduler()
    posted = []

    async def fake_post(vid, title):
        posted.append((vid, title))
        return "post_1"

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "scheduler", sched)
    monkeypatch.setattr(main, "post_live_video", fake_post)
    return store, sched, posted


def _fake_info(monkeypatch, status, scheduled_start=None):
    async def fake_info(vid):
        return status, "Vespers", scheduled_start

    monkeypatch.setattr(main, "get_video_info", fake_info)


@pytest.mark.asyncio
async def test_upcoming_far_ahead_checks_before_scheduled_start(env, monkeypatch):
    store, sched, posted = env
    start = (datetime.now(timezone.utc) + timedelta(days=14)).replace(microsecond=0)
    _fake_info(monkeypatch, "upcoming", start)

    await main.process_video("VID")
    run_date, args = sched.jobs["recheck-VID"]
    assert run_date == start - main._EARLY_START
    assert args == ["VID"]
    assert store.get_pending("VID") == int(start.timestamp())
    assert posted == []


@pytest.mark.asyncio
async def test_upcoming_past_start_polls_soon(env, monkeypatch):
    store, sched, posted = env
    start = datetime.now(timezone.utc) - timedelta(minutes=10)  # running late
    _fake_info(monkeypatch, "upcoming", start)

    await main.process_video("VID")
    run_date, _ = sched.jobs["recheck-VID"]
    assert run_date - datetime.now(timezone.utc) < timedelta(
        seconds=main._POLL_INTERVAL_SECONDS + 5
    )


@pytest.mark.asyncio
async def test_upcoming_gives_up_long_after_start(env, monkeypatch):
    store, sched, posted = env
    start = datetime.now(timezone.utc) - main._LATE_START - timedelta(minutes=1)
    _fake_info(monkeypatch, "upcoming", start)

    await main.process_video("VID")
    assert sched.jobs == {}
    assert store.get_pending("VID") is None


@pytest.mark.asyncio
async def test_pending_video_posts_when_live(env, monkeypatch):
    store, sched, posted = env
    store.set_pending("VID", int(datetime.now(timezone.utc).timestamp()))
    _fake_info(monkeypatch, "live")

    await main.process_video("VID")
    assert posted == [("VID", "Vespers")]
    assert store.already_posted("VID")
    assert store.get_pending("VID") is None


@pytest.mark.asyncio
async def test_pending_video_keeps_polling_on_api_error(env, monkeypatch):
    store, sched, posted = env
    store.set_pending("VID", int(datetime.now(timezone.utc).timestamp()))
    _fake_info(monkeypatch, None)

    await main.process_video("VID")
    assert "recheck-VID" in sched.jobs
    assert store.get_pending("VID") is not None


def test_restore_pending_reschedules(env):
    store, sched, posted = env
    soon = datetime.now(timezone.utc) + timedelta(days=1)
    stale = datetime.now(timezone.utc) - timedelta(days=1)
    store.set_pending("SOON", int(soon.timestamp()))
    store.set_pending("STALE", int(stale.timestamp()))

    main._restore_pending()
    assert set(sched.jobs) == {"recheck-SOON"}
    assert store.get_pending("STALE") is None


@pytest.mark.asyncio
async def test_sync_upcoming_tracks_scheduled_streams(env, monkeypatch):
    store, sched, posted = env
    start = datetime.now(timezone.utc) + timedelta(days=3)

    async def fake_list():
        return ["A", "B"]

    monkeypatch.setattr(main, "list_upcoming_video_ids", fake_list)
    _fake_info(monkeypatch, "upcoming", start)

    await main.sync_upcoming()
    assert set(sched.jobs) == {"recheck-A", "recheck-B"}
    assert store.get_pending("A") is not None
