"""CLI entrypoints for background jobs."""

import argparse
import asyncio

from app.core.logging import configure_logging
from app.core.config import settings
from app.db.mongodb import close_mongo_connection, connect_to_mongo
from app.workers.jobs import (
    run_alert_job,
    run_discovery_job,
    run_scoring_job,
    run_snapshot_job,
    seed_categories,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Project Radar worker")
    parser.add_argument(
        "job",
        choices=["discovery", "snapshots", "scoring", "alerts", "all"],
    )
    args = parser.parse_args()
    configure_logging(settings.debug)
    await connect_to_mongo()
    await seed_categories()
    try:
        if args.job == "discovery":
            print(await run_discovery_job())
        elif args.job == "snapshots":
            print(await run_snapshot_job())
        elif args.job == "scoring":
            print(await run_scoring_job())
        elif args.job == "alerts":
            print(await run_alert_job())
        else:
            print(await run_discovery_job())
            print(await run_snapshot_job())
            print(await run_scoring_job())
            print(await run_alert_job())
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())
