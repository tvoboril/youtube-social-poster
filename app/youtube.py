"""YouTube Data API helpers: check a video's live status and title."""
from __future__ import annotations

import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_API_BASE = "https://www.googleapis.com/youtube/v3"


async def get_video_info(video_id: str) -> tuple[str | None, str | None]:
    """Return ``(liveBroadcastContent, title)`` for a video.

    ``liveBroadcastContent`` is "live", "upcoming", or "none". Returns
    ``(None, None)`` if the video wasn't found (deleted/private) or the API
    errored. Costs 1 quota unit per call.
    """
    params = {
        "part": "snippet,liveStreamingDetails",
        "id": video_id,
        "key": settings.yt_api_key,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{_API_BASE}/videos", params=params)

    if resp.status_code != 200:
        logger.error("YouTube videos.list failed %s: %s", resp.status_code, resp.text)
        return None, None

    items = resp.json().get("items", [])
    if not items:
        logger.info("Video %s not found (private/deleted?)", video_id)
        return None, None

    snippet = items[0].get("snippet", {})
    status = snippet.get("liveBroadcastContent", "none")
    title = snippet.get("title")
    logger.info("Video %s liveBroadcastContent=%s title=%r", video_id, status, title)
    return status, title
