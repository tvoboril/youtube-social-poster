"""FastAPI service: receive YouTube WebSub pushes and post live streams to Facebook."""
from __future__ import annotations

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from xml.etree import ElementTree

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from .config import settings
from .facebook import post_live_video
from .scheduler import scheduler, start_scheduler
from .store import PostedStore
from .websub import subscribe
from .youtube import get_video_info

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("youtube-social-poster")

store = PostedStore(settings.db_path)

# Atom / YouTube XML namespaces used in the notification payload.
_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

# If a stream is still "upcoming" when we're notified, re-check a few times as it
# may flip to "live" shortly after (scheduled streams). Delay in seconds.
_UPCOMING_RECHECK_DELAY = 120
_UPCOMING_MAX_RECHECKS = 15  # ~30 min of coverage


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up; subscribing to WebSub and starting scheduler")
    start_scheduler()
    await subscribe("subscribe")
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="YouTube Social Poster", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/youtube/webhook")
async def verify(request: Request) -> Response:
    """WebSub verification handshake: echo hub.challenge for our topic."""
    params = request.query_params
    mode = params.get("hub.mode")
    topic = params.get("hub.topic")
    challenge = params.get("hub.challenge")

    if challenge and topic == settings.topic_url and mode in ("subscribe", "unsubscribe"):
        logger.info("WebSub verification ok (mode=%s)", mode)
        return PlainTextResponse(challenge)

    logger.warning("WebSub verification rejected: mode=%s topic=%s", mode, topic)
    return Response(status_code=404)


@app.post("/youtube/webhook")
async def notify(request: Request) -> Response:
    body = await request.body()

    if not _valid_signature(request.headers.get("X-Hub-Signature", ""), body):
        logger.warning("Rejected notification: bad HMAC signature")
        return Response(status_code=403)

    video_id, channel_id = _parse_notification(body)
    if not video_id:
        # Could be a <at:deleted-entry> or unparseable body; ack so hub stops retrying.
        return Response(status_code=204)

    if channel_id and channel_id != settings.yt_channel_id:
        logger.info("Ignoring notification for other channel %s", channel_id)
        return Response(status_code=204)

    # Ack the hub immediately; do the slow work without blocking the response.
    import asyncio

    asyncio.create_task(process_video(video_id))
    return Response(status_code=204)


async def process_video(video_id: str, attempt: int = 0) -> None:
    """Post to Facebook if this video is a not-yet-posted live stream."""
    if store.already_posted(video_id):
        logger.info("Video %s already posted; skipping", video_id)
        return

    status, title = await get_video_info(video_id)
    if status == "live":
        try:
            await post_live_video(video_id, title or "Live now")
        except Exception:  # noqa: BLE001 - keep unposted so a later push can retry
            logger.exception("Facebook post failed for %s; not marking as posted", video_id)
            return
        store.mark_posted(video_id)
    elif status == "upcoming" and attempt < _UPCOMING_MAX_RECHECKS:
        logger.info(
            "Video %s upcoming; rechecking in %ss (attempt %s)",
            video_id, _UPCOMING_RECHECK_DELAY, attempt + 1,
        )
        scheduler.add_job(
            process_video,
            "date",
            run_date=_seconds_from_now(_UPCOMING_RECHECK_DELAY),
            args=[video_id, attempt + 1],
            id=f"recheck-{video_id}",
            replace_existing=True,
        )
    else:
        logger.info("Video %s status=%s; nothing to post", video_id, status)


def _seconds_from_now(seconds: int):
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _valid_signature(header: str, body: bytes) -> bool:
    """Verify the X-Hub-Signature: sha1=<hex> header against our shared secret."""
    if not header.startswith("sha1="):
        return False
    expected = hmac.new(
        settings.websub_secret.encode(), body, hashlib.sha1
    ).hexdigest()
    return hmac.compare_digest(expected, header[len("sha1="):])


def _parse_notification(body: bytes) -> tuple[str | None, str | None]:
    """Extract (video_id, channel_id) from the Atom notification body."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        logger.warning("Could not parse notification XML")
        return None, None

    entry = root.find("atom:entry", _NS)
    if entry is None:
        return None, None

    video_el = entry.find("yt:videoId", _NS)
    channel_el = entry.find("yt:channelId", _NS)
    video_id = video_el.text if video_el is not None else None
    channel_id = channel_el.text if channel_el is not None else None
    return video_id, channel_id
