"""Background discovery, snapshot, scoring, and alert workers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.db.mongodb import get_db
from app.repositories.data import (
    AlertRepository,
    RepositoryRepository,
    ScoreRepository,
    SnapshotRepository,
    SyncJobRepository,
)
from app.services.github_client import (
    GitHubClient,
    GitHubRateLimitError,
    build_discovery_queries,
)
from app.services.scoring import calculate_momentum_score, compute_growth_metrics

logger = get_logger(__name__)


async def run_discovery_job() -> dict[str, Any]:
    db = get_db()
    sync_jobs = SyncJobRepository(db)
    repos = RepositoryRepository(db)
    snapshots = SnapshotRepository(db)
    job_id = await sync_jobs.start("discovery")
    client = GitHubClient()
    discovered = 0
    upserted = 0

    try:
        queries = build_discovery_queries()
        seen_ids: set[int] = set()
        for query in queries:
            for page in range(1, settings.github_max_pages + 1):
                try:
                    payload = await client.search_repositories(query, page=page)
                except GitHubRateLimitError as exc:
                    await sync_jobs.finish(
                        job_id,
                        "failed",
                        message=str(exc),
                        stats={"discovered": discovered, "upserted": upserted},
                    )
                    return {"status": "rate_limited", "discovered": discovered}

                items = payload.get("items") or []
                if not items:
                    break
                for item in items:
                    github_id = item["id"]
                    if github_id in seen_ids:
                        continue
                    seen_ids.add(github_id)
                    discovered += 1
                    normalized = GitHubClient.normalize_repository(item)
                    saved = await repos.upsert_from_github(normalized)
                    upserted += 1
                    await snapshots.insert_snapshot(
                        {
                            "repository_id": saved["id"],
                            "github_repository_id": github_id,
                            "stars": normalized["stars"],
                            "forks": normalized["forks"],
                            "open_issues": normalized["open_issues"],
                            "watchers": normalized.get("watchers", 0),
                            "source": "discovery",
                        }
                    )

        stats = {"discovered": discovered, "upserted": upserted, "queries": len(queries)}
        await sync_jobs.finish(job_id, "success", message="Discovery completed", stats=stats)
        logger.info("discovery_job_complete", **stats)
        return {"status": "success", **stats}
    except Exception as exc:  # noqa: BLE001
        logger.error("discovery_job_failed", error=str(exc))
        await sync_jobs.finish(job_id, "failed", message=str(exc))
        return {"status": "failed", "error": str(exc)}


async def run_snapshot_job(limit: int = 300) -> dict[str, Any]:
    db = get_db()
    sync_jobs = SyncJobRepository(db)
    repos = RepositoryRepository(db)
    snapshots = SnapshotRepository(db)
    job_id = await sync_jobs.start("snapshots")
    client = GitHubClient()
    updated = 0
    failed = 0

    try:
        tracked = await repos.list_ids(limit=limit)
        for item in tracked:
            try:
                raw = await client.get_repository_by_id(item["github_repository_id"])
                normalized = GitHubClient.normalize_repository(raw)
                saved = await repos.upsert_from_github(normalized)
                await snapshots.insert_snapshot(
                    {
                        "repository_id": saved["id"],
                        "github_repository_id": normalized["github_repository_id"],
                        "stars": normalized["stars"],
                        "forks": normalized["forks"],
                        "open_issues": normalized["open_issues"],
                        "watchers": normalized.get("watchers", 0),
                        "source": "metrics",
                    }
                )
                updated += 1
            except GitHubRateLimitError as exc:
                await sync_jobs.finish(
                    job_id,
                    "failed",
                    message=str(exc),
                    stats={"updated": updated, "failed": failed},
                )
                return {"status": "rate_limited", "updated": updated, "failed": failed}
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning(
                    "snapshot_repo_failed",
                    repo=item.get("full_name"),
                    error=str(exc),
                )

        stats = {"updated": updated, "failed": failed}
        await sync_jobs.finish(job_id, "success", message="Snapshots completed", stats=stats)
        logger.info("snapshot_job_complete", **stats)
        return {"status": "success", **stats}
    except Exception as exc:  # noqa: BLE001
        logger.error("snapshot_job_failed", error=str(exc))
        await sync_jobs.finish(job_id, "failed", message=str(exc))
        return {"status": "failed", "error": str(exc)}


async def run_scoring_job(limit: int = 500) -> dict[str, Any]:
    db = get_db()
    sync_jobs = SyncJobRepository(db)
    repos = RepositoryRepository(db)
    snapshots = SnapshotRepository(db)
    scores = ScoreRepository(db)
    job_id = await sync_jobs.start("scoring")
    scored = 0

    try:
        tracked = await repos.list_ids(limit=limit)
        for item in tracked:
            repo = await repos.get_by_id(item["id"])
            if not repo:
                continue
            growth = await compute_growth_metrics(
                snapshots,
                repo["id"],
                repo.get("stars", 0),
                repo.get("forks", 0),
            )
            momentum, components, labels = calculate_momentum_score(
                stars=repo.get("stars", 0),
                created_at=repo["created_at"],
                pushed_at=repo.get("pushed_at"),
                open_issues=repo.get("open_issues", 0),
                growth=growth,
            )
            await scores.upsert(
                repo["id"],
                {
                    "github_repository_id": repo["github_repository_id"],
                    "momentum_score": momentum,
                    "components": components,
                    "labels": labels,
                    **growth,
                },
            )
            scored += 1

        stats = {"scored": scored}
        await sync_jobs.finish(job_id, "success", message="Scoring completed", stats=stats)
        logger.info("scoring_job_complete", **stats)
        return {"status": "success", **stats}
    except Exception as exc:  # noqa: BLE001
        logger.error("scoring_job_failed", error=str(exc))
        await sync_jobs.finish(job_id, "failed", message=str(exc))
        return {"status": "failed", "error": str(exc)}


async def run_alert_job() -> dict[str, Any]:
    db = get_db()
    sync_jobs = SyncJobRepository(db)
    alerts = AlertRepository(db)
    repos = RepositoryRepository(db)
    scores = ScoreRepository(db)
    job_id = await sync_jobs.start("alerts")
    triggered = 0

    try:
        active = await alerts.list_active()
        for alert in active:
            repo = await repos.get_by_id(alert["repository_id"])
            score = await scores.get(alert["repository_id"])
            if not repo:
                continue
            value = None
            ctype = alert["condition_type"]
            if ctype == "stars_gained_24h":
                value = (score or {}).get("stars_gained_24h", 0)
            elif ctype == "total_stars":
                value = repo.get("stars", 0)
            elif ctype == "momentum_score":
                value = (score or {}).get("momentum_score", 0)

            if value is not None and value >= alert["threshold"]:
                await alerts.mark_triggered(alert["id"])
                triggered += 1
                logger.info(
                    "alert_triggered",
                    alert_id=alert["id"],
                    repository_id=alert["repository_id"],
                    value=value,
                    threshold=alert["threshold"],
                )

        stats = {"checked": len(active), "triggered": triggered}
        await sync_jobs.finish(job_id, "success", message="Alerts evaluated", stats=stats)
        return {"status": "success", **stats}
    except Exception as exc:  # noqa: BLE001
        logger.error("alert_job_failed", error=str(exc))
        await sync_jobs.finish(job_id, "failed", message=str(exc))
        return {"status": "failed", "error": str(exc)}


async def seed_categories() -> None:
    db = get_db()
    defaults = [
        ("AI Agents", "ai-agents"),
        ("LLM", "llm"),
        ("DevOps", "devops"),
        ("Frontend", "frontend"),
        ("Backend", "backend"),
        ("Databases", "databases"),
        ("Research", "research"),
    ]
    now = datetime.now(timezone.utc)
    for name, slug in defaults:
        await db.categories.update_one(
            {"slug": slug},
            {"$setOnInsert": {"name": name, "slug": slug, "created_at": now}},
            upsert=True,
        )
