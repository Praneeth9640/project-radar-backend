"""Growth calculations and configurable Momentum Score."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings
from app.models.schemas import LabelType
from app.repositories.data import SnapshotRepository


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def growth_between(current: int, previous: int | None) -> tuple[int, float]:
    if previous is None:
        return 0, 0.0
    gained = max(current - previous, 0)
    pct = (gained / previous * 100.0) if previous > 0 else (100.0 if gained > 0 else 0.0)
    return gained, pct


async def compute_growth_metrics(
    snapshots: SnapshotRepository,
    repository_id: str,
    current_stars: int,
    current_forks: int,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    snap_24h = await snapshots.get_nearest(repository_id, now - timedelta(hours=24))
    snap_7d = await snapshots.get_nearest(repository_id, now - timedelta(days=7))
    snap_30d = await snapshots.get_nearest(repository_id, now - timedelta(days=30))

    stars_24h, _ = growth_between(current_stars, snap_24h["stars"] if snap_24h else None)
    stars_7d, pct_7d = growth_between(current_stars, snap_7d["stars"] if snap_7d else None)
    stars_30d, _ = growth_between(current_stars, snap_30d["stars"] if snap_30d else None)
    forks_7d, _ = growth_between(current_forks, snap_7d["forks"] if snap_7d else None)

    return {
        "stars_gained_24h": stars_24h,
        "stars_gained_7d": stars_7d,
        "stars_gained_30d": stars_30d,
        "stars_growth_pct_7d": round(pct_7d, 2),
        "forks_gained_7d": forks_7d,
    }


def calculate_momentum_score(
    *,
    stars: int,
    created_at: datetime,
    pushed_at: datetime | None,
    open_issues: int,
    growth: dict[str, Any],
) -> tuple[float, dict[str, float], list[str]]:
    age_days = max(
        (datetime.now(timezone.utc) - _ensure_aware(created_at)).total_seconds() / 86400,
        0.1,
    )

    star_growth_component = _clamp(growth["stars_gained_7d"] / 50.0 * 100.0)
    star_pct_component = _clamp(growth["stars_growth_pct_7d"])
    recency_component = _clamp(100.0 * (30.0 / (age_days + 30.0)) * 1.5)
    fork_growth_component = _clamp(growth["forks_gained_7d"] / 10.0 * 100.0)

    activity_score = 40.0
    if pushed_at:
        days_since_push = (
            datetime.now(timezone.utc) - _ensure_aware(pushed_at)
        ).total_seconds() / 86400
        activity_score = _clamp(100.0 - days_since_push * 3.0)
    activity_score = _clamp(activity_score + min(open_issues, 20))

    components = {
        "star_growth": round(star_growth_component, 2),
        "star_pct": round(star_pct_component, 2),
        "recency": round(recency_component, 2),
        "fork_growth": round(fork_growth_component, 2),
        "activity": round(activity_score, 2),
    }

    score = (
        components["star_growth"] * settings.momentum_weight_star_growth
        + components["star_pct"] * settings.momentum_weight_star_pct
        + components["recency"] * settings.momentum_weight_recency
        + components["fork_growth"] * settings.momentum_weight_fork_growth
        + components["activity"] * settings.momentum_weight_activity
    )
    score = round(_clamp(score), 2)
    labels = assign_labels(
        score=score,
        age_days=age_days,
        stars=stars,
        growth=growth,
    )
    return score, components, labels


def assign_labels(
    *,
    score: float,
    age_days: float,
    stars: int,
    growth: dict[str, Any],
) -> list[str]:
    labels: list[str] = []
    if score >= 85:
        labels.append(LabelType.HOT.value)
    if growth["stars_gained_7d"] >= 100 and growth["stars_growth_pct_7d"] >= 20:
        labels.append(LabelType.RISING.value)
    if growth["stars_gained_24h"] >= 50 or growth["stars_gained_7d"] >= 200:
        labels.append(LabelType.FAST_GROWING.value)
    if age_days <= 30:
        labels.append(LabelType.NEW.value)
    if stars < 1500 and score >= 70 and growth["stars_gained_7d"] >= 40:
        labels.append(LabelType.HIDDEN_GEM.value)
    # de-dupe while preserving order
    seen = set()
    ordered = []
    for label in labels:
        if label not in seen:
            seen.add(label)
            ordered.append(label)
    return ordered


def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
