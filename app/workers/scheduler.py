"""APScheduler setup for periodic ingestion jobs."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.core.logging import get_logger
from app.workers.jobs import (
    run_alert_job,
    run_discovery_job,
    run_scoring_job,
    run_snapshot_job,
)

logger = get_logger(__name__)
scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    if not settings.enable_scheduler:
        logger.info("scheduler_disabled")
        return
    if scheduler.running:
        return

    scheduler.add_job(
        run_discovery_job,
        "interval",
        minutes=settings.discovery_interval_minutes,
        id="discovery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_snapshot_job,
        "interval",
        minutes=settings.snapshot_interval_minutes,
        id="snapshots",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_scoring_job,
        "interval",
        minutes=settings.scoring_interval_minutes,
        id="scoring",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_alert_job,
        "interval",
        minutes=settings.alert_interval_minutes,
        id="alerts",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("scheduler_started")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
