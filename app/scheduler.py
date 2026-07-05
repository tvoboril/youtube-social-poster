"""Background scheduler: renew the WebSub lease before it expires."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from .websub import subscribe

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _renew() -> None:
    logger.info("Renewing WebSub subscription")
    await subscribe("subscribe")


def start_scheduler() -> None:
    scheduler.add_job(
        _renew,
        "interval",
        seconds=settings.renew_interval_seconds,
        id="websub-renew",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started; renewing WebSub every %s seconds",
        settings.renew_interval_seconds,
    )
