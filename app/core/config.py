"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the Project Radar API and workers."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Open-Source Project Radar"
    environment: str = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"

    # Override via MONGODB_URI in .env (MongoDB Atlas recommended).
    mongodb_uri: str = (
        "mongodb+srv://USER:PASSWORD@CLUSTER.mongodb.net/"
        "project_radar?retryWrites=true&w=majority"
    )
    mongodb_db_name: str = "project_radar"

    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7

    # Clerk (frontend auth). Optional issuer e.g. https://xxx.clerk.accounts.dev
    clerk_secret_key: str = ""
    clerk_publishable_key: str = ""
    clerk_issuer: str = ""

    github_token: str = ""
    github_api_base: str = "https://api.github.com"
    github_per_page: int = 30
    github_max_pages: int = 3

    discovery_languages: str = "Python,TypeScript,JavaScript,Go,Rust"
    discovery_topics: str = "ai,llm,agent,devtools,framework"
    discovery_min_stars: int = 10
    discovery_max_age_days: int = 365
    discovery_created_within_days: int = 90

    snapshot_interval_minutes: int = 45
    discovery_interval_minutes: int = 60
    scoring_interval_minutes: int = 30
    alert_interval_minutes: int = 15
    enable_scheduler: bool = True

    # TokenRouter (OpenAI-compatible). Set OPENAI_API_KEY in .env manually.
    openai_api_key: str = ""
    openai_api_base: str = "https://api.tokenrouter.com/v1"
    openai_model: str = "z-ai/glm-4-9b-chat"
    openai_fallback_model: str = "nvidia/nemotron-3-8b-instruct:free"
    ai_enabled: bool = True

    @property
    def ai_model_list(self) -> List[str]:
        models = [self.openai_model, self.openai_fallback_model]
        return [m.strip() for m in models if m and m.strip()]

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    momentum_weight_star_growth: float = 0.35
    momentum_weight_star_pct: float = 0.25
    momentum_weight_recency: float = 0.15
    momentum_weight_fork_growth: float = 0.10
    momentum_weight_activity: float = 0.15

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def language_list(self) -> List[str]:
        return [x.strip() for x in self.discovery_languages.split(",") if x.strip()]

    @property
    def topic_list(self) -> List[str]:
        return [x.strip() for x in self.discovery_topics.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
