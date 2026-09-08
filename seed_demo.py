"""Seed demo repositories when GitHub token is unavailable."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.mongodb import close_mongo_connection, connect_to_mongo, get_db
from app.repositories.data import RepositoryRepository, ScoreRepository, SnapshotRepository
from app.services.scoring import calculate_momentum_score, compute_growth_metrics
from app.workers.jobs import seed_categories


DEMO_REPOS = [
    {
        "github_repository_id": 900001,
        "full_name": "signalkit/early-agent",
        "owner": "signalkit",
        "name": "early-agent",
        "description": "Lightweight agent framework optimized for tool-calling workflows.",
        "html_url": "https://github.com/signalkit/early-agent",
        "language": "TypeScript",
        "topics": ["ai", "agent", "llm"],
        "license": "MIT",
        "stars": 820,
        "forks": 64,
        "open_issues": 12,
        "watchers": 820,
        "age_days": 18,
        "growth_7d": 210,
    },
    {
        "github_repository_id": 900002,
        "full_name": "vectorlane/fast-rag",
        "owner": "vectorlane",
        "name": "fast-rag",
        "description": "High-throughput retrieval pipeline for production RAG systems.",
        "html_url": "https://github.com/vectorlane/fast-rag",
        "language": "Python",
        "topics": ["rag", "llm", "search"],
        "license": "Apache-2.0",
        "stars": 1450,
        "forks": 120,
        "open_issues": 28,
        "watchers": 1450,
        "age_days": 40,
        "growth_7d": 320,
    },
    {
        "github_repository_id": 900003,
        "full_name": "shipyard/devtools-radar",
        "owner": "shipyard",
        "name": "devtools-radar",
        "description": "CLI and dashboard for spotting emerging developer tools.",
        "html_url": "https://github.com/shipyard/devtools-radar",
        "language": "Go",
        "topics": ["devtools", "cli"],
        "license": "MIT",
        "stars": 310,
        "forks": 22,
        "open_issues": 5,
        "watchers": 310,
        "age_days": 9,
        "growth_7d": 140,
    },
]


async def main() -> None:
    configure_logging(settings.debug)
    await connect_to_mongo()
    await seed_categories()
    db = get_db()
    repos = RepositoryRepository(db)
    snapshots = SnapshotRepository(db)
    scores = ScoreRepository(db)
    now = datetime.now(timezone.utc)

    for demo in DEMO_REPOS:
        created_at = now - timedelta(days=demo["age_days"])
        payload = {
            "github_repository_id": demo["github_repository_id"],
            "full_name": demo["full_name"],
            "owner": demo["owner"],
            "name": demo["name"],
            "description": demo["description"],
            "html_url": demo["html_url"],
            "created_at": created_at,
            "updated_at": now,
            "pushed_at": now - timedelta(hours=8),
            "stars": demo["stars"],
            "forks": demo["forks"],
            "open_issues": demo["open_issues"],
            "watchers": demo["watchers"],
            "language": demo["language"],
            "topics": demo["topics"],
            "license": demo["license"],
        }
        saved = await repos.upsert_from_github(payload)
        await snapshots.insert_snapshot(
            {
                "repository_id": saved["id"],
                "github_repository_id": demo["github_repository_id"],
                "stars": max(demo["stars"] - demo["growth_7d"], 1),
                "forks": max(demo["forks"] - 10, 0),
                "open_issues": demo["open_issues"],
                "watchers": max(demo["stars"] - demo["growth_7d"], 1),
                "captured_at": now - timedelta(days=7),
                "source": "seed",
            }
        )
        await snapshots.insert_snapshot(
            {
                "repository_id": saved["id"],
                "github_repository_id": demo["github_repository_id"],
                "stars": demo["stars"],
                "forks": demo["forks"],
                "open_issues": demo["open_issues"],
                "watchers": demo["watchers"],
                "captured_at": now,
                "source": "seed",
            }
        )
        growth = await compute_growth_metrics(
            snapshots, saved["id"], demo["stars"], demo["forks"]
        )
        momentum, components, labels = calculate_momentum_score(
            stars=demo["stars"],
            created_at=created_at,
            pushed_at=now - timedelta(hours=8),
            open_issues=demo["open_issues"],
            growth=growth,
        )
        await scores.upsert(
            saved["id"],
            {
                "github_repository_id": demo["github_repository_id"],
                "momentum_score": momentum,
                "components": components,
                "labels": labels,
                **growth,
            },
        )
        print(f"seeded {demo['full_name']} score={momentum}")

    await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())
