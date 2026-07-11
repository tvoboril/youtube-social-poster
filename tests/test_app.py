import hashlib
import hmac

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
    monkeypatch.setattr(settings, "post_template", "{title}\\n\\nWatch live.")
    assert settings.render_message("Divine Liturgy") == "Divine Liturgy\n\nWatch live."


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
        return "live", "Sunday Divine Liturgy"

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
        return "none", None

    async def fake_post(vid, title):
        posted.append(vid)

    monkeypatch.setattr(main, "get_video_info", fake_info)
    monkeypatch.setattr(main, "post_live_video", fake_post)

    await main.process_video("REGULARVID")
    assert posted == []
    assert store.already_posted("REGULARVID") is False
