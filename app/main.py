"""FastAPI service: receive YouTube WebSub pushes and post live streams to Facebook."""
from __future__ import annotations

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from .config import settings
from .facebook import post_live_video
from .scheduler import scheduler, start_scheduler
from .store import PostedStore
from .websub import subscribe
from .youtube import get_video_info, list_upcoming_video_ids

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("youtube-social-poster")

store = PostedStore(settings.db_path)

# Atom / YouTube XML namespaces used in the notification payload.
_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

# YouTube notifies us when a stream is scheduled (often weeks ahead) but usually
# not when it goes live, so for an "upcoming" video we check it ourselves: starting
# shortly before its scheduled start (streams often begin a few minutes early),
# then every _POLL_INTERVAL_SECONDS until it's live, giving up _LATE_START after
# the scheduled start.
_POLL_INTERVAL_SECONDS = 120
_EARLY_START = timedelta(minutes=15)
_LATE_START = timedelta(hours=3)
# Also look up the channel's upcoming streams directly (at startup and on this
# interval) in case a WebSub notification was missed.
_SYNC_UPCOMING_INTERVAL_SECONDS = 6 * 3600


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up; subscribing to WebSub and starting scheduler")
    start_scheduler()
    _restore_pending()
    scheduler.add_job(
        sync_upcoming,
        "interval",
        seconds=_SYNC_UPCOMING_INTERVAL_SECONDS,
        next_run_time=datetime.now(timezone.utc),
        id="sync-upcoming",
        replace_existing=True,
    )
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


async def process_video(video_id: str) -> None:
    """Post to Facebook if this video is a not-yet-posted live stream.

    Upcoming streams are remembered and re-checked around their scheduled start.
    """
    if store.already_posted(video_id):
        logger.info("Video %s already posted; skipping", video_id)
        store.clear_pending(video_id)
        return

    status, title, scheduled_start = await get_video_info(video_id)
    if status == "live":
        try:
            await post_live_video(video_id, title or "Live now")
        except Exception:  # noqa: BLE001 - keep unposted so a later push can retry
            logger.exception("Facebook post failed for %s; not marking as posted", video_id)
            return
        store.mark_posted(video_id)
        store.clear_pending(video_id)
    elif status == "upcoming" or (
        status is None and store.get_pending(video_id) is not None
    ):
        # status None = API error / not found; keep checking a known pending stream.
        if status == "upcoming":
            start = scheduled_start or datetime.now(timezone.utc)
            store.set_pending(video_id, int(start.timestamp()))
        _schedule_check(video_id, store.get_pending(video_id))
    else:
        logger.info("Video %s status=%s; nothing to post", video_id, status)
        store.clear_pending(video_id)


def _schedule_check(video_id: str, scheduled_start: int) -> None:
    """Schedule the next live check for a pending video, or give up if too late."""
    now = datetime.now(timezone.utc)
    start = datetime.fromtimestamp(scheduled_start, timezone.utc)
    if now > start + _LATE_START:
        logger.info("Video %s never went live (scheduled %s); giving up", video_id, start)
        store.clear_pending(video_id)
        return

    run_at = max(start - _EARLY_START, now + timedelta(seconds=_POLL_INTERVAL_SECONDS))
    logger.info(
        "Video %s upcoming (scheduled %s); next check at %s",
        video_id, start.isoformat(), run_at.isoformat(timespec="seconds"),
    )
    scheduler.add_job(
        process_video,
        "date",
        run_date=run_at,
        args=[video_id],
        id=f"recheck-{video_id}",
        replace_existing=True,
        misfire_grace_time=None,
    )


async def sync_upcoming() -> None:
    """Pick up the channel's scheduled streams, whether or not we were notified."""
    video_ids = await list_upcoming_video_ids()
    logger.info("Found %s upcoming stream(s) on the channel", len(video_ids))
    for video_id in video_ids:
        await process_video(video_id)


def _restore_pending() -> None:
    """Re-schedule checks for upcoming videos remembered across a restart."""
    for video_id, scheduled_start in store.all_pending():
        _schedule_check(video_id, scheduled_start)


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
