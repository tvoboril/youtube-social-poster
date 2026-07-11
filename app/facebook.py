"""Publish a post to the parish Facebook Page via the Graph API."""
from __future__ import annotations

import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)


async def post_to_page(message: str, link: str | None = None) -> str:
    """Publish `message` (with an optional `link`) to the Page feed.

    Passing `link` makes Facebook render a link-preview card from that URL
    without the raw URL appearing in the message text. Returns the new post id.
    Raises httpx.HTTPStatusError on failure so the caller can avoid marking the
    video as posted (and thus retry on the next notification).
    """
    url = (
        f"https://graph.facebook.com/{settings.fb_api_version}"
        f"/{settings.fb_page_id}/feed"
    )
    data = {"message": message, "access_token": settings.fb_page_token}
    if link:
        data["link"] = link

    # Facebook synchronously scrapes the link to build the preview card, so the
    # response can be slow even though the post succeeds. Use a generous read
    # timeout to avoid a false failure (which would leave the post unrecorded and
    # risk a duplicate on the next notification).
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, data=data)

    if resp.status_code != 200:
        logger.error("Facebook post failed %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    post_id = resp.json().get("id", "")
    logger.info("Published Facebook post %s", post_id)
    return post_id


async def post_live_video(video_id: str, title: str) -> str:
    """Render the message from the title and publish it with the video as a link."""
    message = settings.render_message(title)
    return await post_to_page(message, link=settings.video_url(video_id))
