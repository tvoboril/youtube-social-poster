"""YouTube Data API helpers: check whether a video is currently live."""
from __future__ import annotations

import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_API_BASE = "https://www.googleapis.com/youtube/v3"


async def get_live_status(video_id: str) -> str | None:
    """Return the video's liveBroadcastContent: "live", "upcoming", "none", or None.

    None means the video wasn't found (deleted/private) or the API errored.
    Costs 1 quota unit per call.
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
        return None

    items = resp.json().get("items", [])
    if not items:
        logger.info("Video %s not found (private/deleted?)", video_id)
        return None

    snippet = items[0].get("snippet", {})
    status = snippet.get("liveBroadcastContent", "none")
    logger.info("Video %s liveBroadcastContent=%s", video_id, status)
    return status


async def is_live(video_id: str) -> bool:
    """True if the video is a live broadcast that is on the air right now."""
    return (await get_live_status(video_id)) == "live"
