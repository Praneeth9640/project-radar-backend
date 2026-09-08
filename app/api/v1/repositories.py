"""Repository listing, details, rankings, saved, notes, analyze, alerts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_current_user_optional, get_database
from app.core.config import settings
from app.models.schemas import (
    AIAnalysisOut,
    AlertCreate,
    AlertOut,
    DashboardOverview,
    MessageOut,
    NoteRequest,
    RepositoryDetailOut,
    RepositoryListResponse,
    RepositoryOut,
    SaveRepositoryRequest,
    ScoreComponents,
    ScoreOut,
    SnapshotOut,
)
from app.repositories.data import (
    AIAnalysisRepository,
    AlertRepository,
    NoteRepository,
    RepositoryRepository,
    SavedRepository,
    ScoreRepository,
    SnapshotRepository,
    SyncJobRepository,
)
from app.services.ai_analysis import DISCLAIMER, generate_repository_analysis
from app.workers.jobs import (
    run_alert_job,
    run_discovery_job,
    run_scoring_job,
    run_snapshot_job,
)

router = APIRouter(tags=["repositories"])


def _to_repo_out(doc: dict, saved_ids: set[str] | None = None) -> RepositoryOut:
    created = doc.get("created_at")
    age_days = doc.get("age_days")
    if age_days is None and isinstance(created, datetime):
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created).total_seconds() / 86400
    return RepositoryOut(
        id=doc["id"],
        github_repository_id=doc["github_repository_id"],
        full_name=doc["full_name"],
        owner=doc["owner"],
        name=doc["name"],
        description=doc.get("description"),
        html_url=doc["html_url"],
        created_at=doc["created_at"],
        updated_at=doc.get("updated_at"),
        pushed_at=doc.get("pushed_at"),
        stars=doc.get("stars", 0),
        forks=doc.get("forks", 0),
        open_issues=doc.get("open_issues", 0),
        watchers=doc.get("watchers", 0),
        language=doc.get("language"),
        topics=doc.get("topics") or [],
        license=doc.get("license"),
        discovered_at=doc.get("discovered_at"),
        last_synced_at=doc.get("last_synced_at"),
        age_days=age_days,
        momentum_score=doc.get("momentum_score"),
        stars_gained_24h=doc.get("stars_gained_24h"),
        stars_gained_7d=doc.get("stars_gained_7d"),
        stars_gained_30d=doc.get("stars_gained_30d"),
        stars_growth_pct_7d=doc.get("stars_growth_pct_7d"),
        forks_gained_7d=doc.get("forks_gained_7d"),
        labels=doc.get("labels") or [],
        saved=bool(saved_ids and doc["id"] in saved_ids),
    )


async def _saved_ids_for(user, db) -> set[str]:
    if not user:
        return set()
    return await SavedRepository(db).saved_ids(user["id"])


@router.get("/repositories", response_model=RepositoryListResponse)
async def list_repositories(
    q: Optional[str] = None,
    language: Optional[str] = None,
    topic: Optional[str] = None,
    min_stars: Optional[int] = None,
    max_age_days: Optional[int] = None,
    min_momentum: Optional[float] = None,
    label: Optional[str] = None,
    sort: str = Query("momentum"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user_optional),
):
    repos = RepositoryRepository(db)
    items, total = await repos.search(
        q=q,
        language=language,
        topic=topic,
        min_stars=min_stars,
        max_age_days=max_age_days,
        min_momentum=min_momentum,
        label=label,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    saved_ids = await _saved_ids_for(user, db)
    return RepositoryListResponse(
        items=[_to_repo_out(i, saved_ids) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/repositories/{repository_id}", response_model=RepositoryDetailOut)
async def get_repository(
    repository_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user_optional),
):
    repos = RepositoryRepository(db)
    repo = await repos.get_by_id(repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    score_doc = await ScoreRepository(db).get(repository_id)
    snaps = await SnapshotRepository(db).list_for_repository(repository_id, limit=90)
    ai = await AIAnalysisRepository(db).latest(repository_id)
    note = None
    tags: list[str] = []
    saved = False
    if user:
        saved_repo = SavedRepository(db)
        saved = await saved_repo.is_saved(user["id"], repository_id)
        note_doc = await NoteRepository(db).get(user["id"], repository_id)
        note = note_doc["content"] if note_doc else None
        # tags from saved record
        saved_list = await saved_repo.list_for_user(user["id"])
        for s in saved_list:
            if s["id"] == repository_id:
                tags = s.get("tags") or []
                break

    flat = {**repo}
    if score_doc:
        flat.update(
            {
                "momentum_score": score_doc.get("momentum_score"),
                "stars_gained_24h": score_doc.get("stars_gained_24h"),
                "stars_gained_7d": score_doc.get("stars_gained_7d"),
                "stars_gained_30d": score_doc.get("stars_gained_30d"),
                "stars_growth_pct_7d": score_doc.get("stars_growth_pct_7d"),
                "forks_gained_7d": score_doc.get("forks_gained_7d"),
                "labels": score_doc.get("labels") or [],
            }
        )
    base = _to_repo_out(flat, {repository_id} if saved else set())
    score_out = None
    if score_doc:
        comps = score_doc.get("components") or {}
        score_out = ScoreOut(
            momentum_score=score_doc.get("momentum_score", 0),
            stars_gained_24h=score_doc.get("stars_gained_24h", 0),
            stars_gained_7d=score_doc.get("stars_gained_7d", 0),
            stars_gained_30d=score_doc.get("stars_gained_30d", 0),
            stars_growth_pct_7d=score_doc.get("stars_growth_pct_7d", 0),
            forks_gained_7d=score_doc.get("forks_gained_7d", 0),
            labels=score_doc.get("labels") or [],
            components=ScoreComponents(**comps) if comps else ScoreComponents(),
            calculated_at=score_doc.get("calculated_at"),
        )

    return RepositoryDetailOut(
        **base.model_dump(),
        score=score_out,
        snapshots=[
            SnapshotOut(
                captured_at=s["captured_at"],
                stars=s["stars"],
                forks=s["forks"],
                open_issues=s.get("open_issues", 0),
                watchers=s.get("watchers", 0),
            )
            for s in reversed(snaps)
        ],
        ai_analysis=ai["summary"] if ai else None,
        note=note,
        tags=tags,
    )


@router.get("/trending", response_model=RepositoryListResponse)
async def trending(
    page: int = 1,
    page_size: int = 20,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user_optional),
):
    return await list_repositories(
        sort="momentum", page=page, page_size=page_size, db=db, user=user
    )


@router.get("/fastest-growing", response_model=RepositoryListResponse)
async def fastest_growing(
    window: str = Query("7d", pattern="^(24h|7d|30d)$"),
    page: int = 1,
    page_size: int = 20,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user_optional),
):
    sort = "star_growth"
    # Prefer label when asking for short-term velocity
    label = "FAST GROWING" if window == "24h" else None
    return await list_repositories(
        sort=sort,
        label=label,
        page=page,
        page_size=page_size,
        db=db,
        user=user,
    )


@router.get("/new", response_model=RepositoryListResponse)
async def new_projects(
    page: int = 1,
    page_size: int = 20,
    language: Optional[str] = None,
    min_stars: Optional[int] = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user_optional),
):
    return await list_repositories(
        language=language,
        min_stars=min_stars,
        max_age_days=settings.discovery_created_within_days,
        sort="age",
        page=page,
        page_size=page_size,
        db=db,
        user=user,
    )


@router.get("/dashboard", response_model=DashboardOverview)
async def dashboard(
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user_optional),
):
    trending_resp = await list_repositories(sort="momentum", page=1, page_size=6, db=db, user=user)
    new_resp = await list_repositories(
        max_age_days=settings.discovery_created_within_days,
        sort="age",
        page=1,
        page_size=6,
        db=db,
        user=user,
    )
    fast_resp = await list_repositories(sort="star_growth", page=1, page_size=6, db=db, user=user)
    saved_items: list[RepositoryOut] = []
    saved_total = 0
    if user:
        saved_docs = await SavedRepository(db).list_for_user(user["id"])
        saved_total = len(saved_docs)
        saved_items = [_to_repo_out(d, {d["id"]}) for d in saved_docs[:6]]
    return DashboardOverview(
        trending=trending_resp.items,
        new_projects=new_resp.items,
        fastest_growing=fast_resp.items,
        saved=saved_items,
        trending_total=trending_resp.total,
        new_projects_total=new_resp.total,
        fastest_growing_total=fast_resp.total,
        saved_total=saved_total,
    )


@router.post("/saved", response_model=MessageOut)
async def save_repository(
    payload: SaveRepositoryRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user),
):
    repo = await RepositoryRepository(db).get_by_id(payload.repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    await SavedRepository(db).save(user["id"], payload.repository_id, payload.tags)
    return MessageOut(message="Repository saved")


@router.delete("/saved/{repository_id}", response_model=MessageOut)
async def unsave_repository(
    repository_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user),
):
    ok = await SavedRepository(db).unsave(user["id"], repository_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Saved repository not found")
    return MessageOut(message="Repository removed from saved")


@router.get("/saved", response_model=list[RepositoryOut])
async def list_saved(
    q: Optional[str] = None,
    tag: Optional[str] = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user),
):
    docs = await SavedRepository(db).list_for_user(user["id"], q=q, tag=tag)
    return [_to_repo_out(d, {d["id"]}) for d in docs]


@router.post("/notes", response_model=MessageOut)
async def upsert_note(
    payload: NoteRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user),
):
    repo = await RepositoryRepository(db).get_by_id(payload.repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    await NoteRepository(db).upsert(user["id"], payload.repository_id, payload.content)
    return MessageOut(message="Note saved")


@router.post("/analyze/{repository_id}", response_model=AIAnalysisOut)
async def analyze_repository(
    repository_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user),
):
    _ = user
    repos = RepositoryRepository(db)
    repo = await repos.get_by_id(repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    score = await ScoreRepository(db).get(repository_id)
    payload = {**repo, **(score or {})}
    summary = await generate_repository_analysis(payload)
    saved = await AIAnalysisRepository(db).create(
        repository_id,
        summary,
        settings.openai_model if settings.openai_api_key else "fallback",
    )
    return AIAnalysisOut(
        repository_id=repository_id,
        summary=summary,
        model=saved["model"],
        created_at=saved["created_at"],
        disclaimer=DISCLAIMER,
    )


@router.post("/alerts", response_model=AlertOut)
async def create_alert(
    payload: AlertCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user),
):
    repo = await RepositoryRepository(db).get_by_id(payload.repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    created = await AlertRepository(db).create(
        user["id"],
        {
            "repository_id": payload.repository_id,
            "condition_type": payload.condition_type.value,
            "threshold": payload.threshold,
            "label": payload.label,
        },
    )
    return AlertOut(**created)


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user),
):
    docs = await AlertRepository(db).list_for_user(user["id"])
    return [AlertOut(**d) for d in docs]


@router.delete("/alerts/{alert_id}", response_model=MessageOut)
async def delete_alert(
    alert_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    user=Depends(get_current_user),
):
    ok = await AlertRepository(db).delete(user["id"], alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alert not found")
    return MessageOut(message="Alert deleted")


@router.get("/sync/status")
async def sync_status(db: AsyncIOMotorDatabase = Depends(get_database)):
    jobs = SyncJobRepository(db)
    return {
        "discovery": await jobs.latest("discovery"),
        "snapshots": await jobs.latest("snapshots"),
        "scoring": await jobs.latest("scoring"),
        "alerts": await jobs.latest("alerts"),
    }


@router.post("/sync/run/{job_type}")
async def run_sync(job_type: str):
    mapping = {
        "discovery": run_discovery_job,
        "snapshots": run_snapshot_job,
        "scoring": run_scoring_job,
        "alerts": run_alert_job,
    }
    if job_type not in mapping:
        raise HTTPException(status_code=400, detail="Unknown job type")
    result = await mapping[job_type]()
    return result
