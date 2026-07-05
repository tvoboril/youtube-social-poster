"""Subscribe to (and renew) the YouTube WebSub / PubSubHubbub feed.

YouTube pushes an Atom notification to our callback whenever the channel
publishes or updates a video. Subscriptions are leased (~5-10 days) so we
renew periodically.
"""
from __future__ import annotations

import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_HUB = "https://pubsubhubbub.appspot.com/subscribe"


async def subscribe(mode: str = "subscribe") -> bool:
    """Send a subscribe/unsubscribe request to the hub.

    The hub responds 202 Accepted, then asynchronously calls our GET
    /youtube/webhook to verify the subscription (challenge handshake).
    """
    data = {
        "hub.callback": settings.callback_url,
        "hub.topic": settings.topic_url,
        "hub.verify": "async",
        "hub.mode": mode,
        "hub.secret": settings.websub_secret,
        # Ask for the max lease; hub may shorten it.
        "hub.lease_seconds": "864000",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(_HUB, data=data)

    if resp.status_code in (202, 204):
        logger.info("WebSub %s request accepted for %s", mode, settings.topic_url)
        return True

    logger.error("WebSub %s failed %s: %s", mode, resp.status_code, resp.text)
    return False
