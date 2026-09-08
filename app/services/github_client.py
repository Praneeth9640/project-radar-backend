"""Authenticated GitHub API client with rate-limit awareness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class GitHubRateLimitError(Exception):
    """Raised when GitHub returns HTTP 403/429 rate-limit responses."""

    def __init__(self, message: str, reset_at: Optional[datetime] = None):
        super().__init__(message)
        self.reset_at = reset_at


class GitHubClient:
    def __init__(self, token: str | None = None):
        self.token = token or settings.github_token
        self.base_url = settings.github_api_base.rstrip("/")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "open-source-project-radar",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._headers = headers

    def _handle_rate_limit(self, response: httpx.Response) -> None:
        if response.status_code in (403, 429):
            reset = response.headers.get("X-RateLimit-Reset")
            remaining = response.headers.get("X-RateLimit-Remaining")
            reset_at = None
            if reset:
                reset_at = datetime.fromtimestamp(int(reset), tz=timezone.utc)
            if remaining == "0" or response.status_code == 429 or "rate limit" in response.text.lower():
                logger.warning(
                    "github_rate_limited",
                    status=response.status_code,
                    reset_at=str(reset_at),
                )
                raise GitHubRateLimitError("GitHub API rate limit exceeded", reset_at)

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._headers, params=params)
            self._handle_rate_limit(response)
            response.raise_for_status()
            return response.json()

    async def search_repositories(self, query: str, page: int = 1, per_page: int | None = None):
        return await self._get(
            "/search/repositories",
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "page": page,
                "per_page": per_page or settings.github_per_page,
            },
        )

    async def get_repository(self, full_name: str):
        return await self._get(f"/repos/{full_name}")

    async def get_repository_by_id(self, github_repository_id: int):
        return await self._get(f"/repositories/{github_repository_id}")

    @staticmethod
    def normalize_repository(raw: dict[str, Any]) -> dict[str, Any]:
        license_info = raw.get("license") or {}
        owner = raw.get("owner") or {}
        created_at = _parse_dt(raw.get("created_at"))
        updated_at = _parse_dt(raw.get("updated_at"))
        pushed_at = _parse_dt(raw.get("pushed_at"))
        return {
            "github_repository_id": raw["id"],
            "full_name": raw["full_name"],
            "owner": owner.get("login") or raw["full_name"].split("/")[0],
            "name": raw["name"],
            "description": raw.get("description"),
            "html_url": raw["html_url"],
            "created_at": created_at,
            "updated_at": updated_at,
            "pushed_at": pushed_at,
            "stars": raw.get("stargazers_count", 0),
            "forks": raw.get("forks_count", 0),
            "open_issues": raw.get("open_issues_count", 0),
            "watchers": raw.get("watchers_count", 0),
            "language": raw.get("language"),
            "topics": raw.get("topics") or [],
            "license": license_info.get("spdx_id") or license_info.get("name"),
            "default_branch": raw.get("default_branch"),
            "archived": raw.get("archived", False),
            "disabled": raw.get("disabled", False),
        }


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_discovery_queries() -> list[str]:
    """Build configurable GitHub search queries for discovery."""
    now = datetime.now(timezone.utc)
    created_after = (now - timedelta(days=settings.discovery_created_within_days)).date()
    queries: list[str] = []

    for language in settings.language_list:
        queries.append(
            f"language:{language} created:>={created_after} stars:>={settings.discovery_min_stars}"
        )

    for topic in settings.topic_list:
        queries.append(
            f"topic:{topic} created:>={created_after} stars:>={settings.discovery_min_stars}"
        )

    # Rapid recent growth heuristic: recently pushed + decent stars
    pushed_after = (now - timedelta(days=7)).date()
    queries.append(
        f"pushed:>={pushed_after} stars:>={max(settings.discovery_min_stars, 50)} sort:stars"
    )
    return queries
