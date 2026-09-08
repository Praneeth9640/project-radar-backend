"""Shared Pydantic schemas and enums."""

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class PyObjectId(str):
    """String wrapper used for Mongo ObjectId serialization."""


class LabelType(str, Enum):
    HOT = "HOT"
    RISING = "RISING"
    FAST_GROWING = "FAST GROWING"
    NEW = "NEW"
    HIDDEN_GEM = "HIDDEN GEM"


class AlertConditionType(str, Enum):
    STARS_GAINED_24H = "stars_gained_24h"
    TOTAL_STARS = "total_stars"
    MOMENTUM_SCORE = "momentum_score"


class SyncJobStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: EmailStr
    display_name: Optional[str] = None
    created_at: datetime


class RepositoryOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    github_repository_id: int
    full_name: str
    owner: str
    name: str
    description: Optional[str] = None
    html_url: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    pushed_at: Optional[datetime] = None
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    watchers: int = 0
    language: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    license: Optional[str] = None
    discovered_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None
    age_days: Optional[float] = None
    momentum_score: Optional[float] = None
    stars_gained_24h: Optional[int] = None
    stars_gained_7d: Optional[int] = None
    stars_gained_30d: Optional[int] = None
    stars_growth_pct_7d: Optional[float] = None
    forks_gained_7d: Optional[int] = None
    labels: List[str] = Field(default_factory=list)
    saved: bool = False


class RepositoryListResponse(BaseModel):
    items: List[RepositoryOut]
    total: int
    page: int
    page_size: int


class SnapshotOut(BaseModel):
    captured_at: datetime
    stars: int
    forks: int
    open_issues: int = 0
    watchers: int = 0


class ScoreComponents(BaseModel):
    star_growth: float = 0.0
    star_pct: float = 0.0
    recency: float = 0.0
    fork_growth: float = 0.0
    activity: float = 0.0


class ScoreOut(BaseModel):
    momentum_score: float
    stars_gained_24h: int = 0
    stars_gained_7d: int = 0
    stars_gained_30d: int = 0
    stars_growth_pct_7d: float = 0.0
    forks_gained_7d: int = 0
    labels: List[str] = Field(default_factory=list)
    components: ScoreComponents = Field(default_factory=ScoreComponents)
    calculated_at: Optional[datetime] = None


class RepositoryDetailOut(RepositoryOut):
    score: Optional[ScoreOut] = None
    snapshots: List[SnapshotOut] = Field(default_factory=list)
    ai_analysis: Optional[dict[str, Any]] = None
    note: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class SaveRepositoryRequest(BaseModel):
    repository_id: str
    tags: List[str] = Field(default_factory=list)


class NoteRequest(BaseModel):
    repository_id: str
    content: str = Field(max_length=5000)


class AlertCreate(BaseModel):
    repository_id: str
    condition_type: AlertConditionType
    threshold: float
    label: Optional[str] = None


class AlertOut(BaseModel):
    id: str
    repository_id: str
    condition_type: AlertConditionType
    threshold: float
    label: Optional[str] = None
    active: bool = True
    triggered: bool = False
    triggered_at: Optional[datetime] = None
    created_at: datetime


class AIAnalysisOut(BaseModel):
    repository_id: str
    summary: dict[str, Any]
    model: str
    created_at: datetime
    disclaimer: str = (
        "Generated analysis — not verified GitHub facts."
    )


class DashboardOverview(BaseModel):
    trending: List[RepositoryOut]
    new_projects: List[RepositoryOut]
    fastest_growing: List[RepositoryOut]
    saved: List[RepositoryOut]
    trending_total: int = 0
    new_projects_total: int = 0
    fastest_growing_total: int = 0
    saved_total: int = 0


class MessageOut(BaseModel):
    message: str
