"""
scheduler.py — Periodic job that purges expired files.
"""

import logging
from telegram.ext import Application

logger = logging.getLogger(__name__)

PURGE_INTERVAL_SECONDS = 3600  # run every hour


async def purge_job(context) -> None:
    db = context.application.bot_data.get("db")
    if db is None:
        return
    count = db.purge_expired()
    if count:
        logger.info("Scheduler: purged %d expired file(s).", count)
    else:
        logger.debug("Scheduler: no expired files found.")


async def start_scheduler(application: Application) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        logger.warning("JobQueue not available. Install python-telegram-bot[job-queue].")
        return
    job_queue.run_repeating(
        purge_job,
        interval=PURGE_INTERVAL_SECONDS,
        first=10,
        name="purge_expired_files",
    )
    logger.info("Purge scheduler started (every %ds).", PURGE_INTERVAL_SECONDS)
