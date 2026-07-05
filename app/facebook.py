"""Publish a post to the parish Facebook Page via the Graph API."""
from __future__ import annotations

import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)


async def post_to_page(message: str) -> str:
    """Publish `message` to the Page feed. Returns the new post id.

    Raises httpx.HTTPStatusError on failure so the caller can avoid marking the
    video as posted (and thus retry on the next notification).
    """
    url = (
        f"https://graph.facebook.com/{settings.fb_api_version}"
        f"/{settings.fb_page_id}/feed"
    )
    data = {"message": message, "access_token": settings.fb_page_token}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=data)

    if resp.status_code != 200:
        logger.error("Facebook post failed %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    post_id = resp.json().get("id", "")
    logger.info("Published Facebook post %s", post_id)
    return post_id


async def post_live_video(video_id: str) -> str:
    """Format the configured template with the live video URL and publish it."""
    message = settings.post_template.format(url=settings.video_url(video_id))
    return await post_to_page(message)
