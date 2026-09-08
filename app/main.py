"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, repositories
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.mongodb import close_mongo_connection, connect_to_mongo
from app.workers.jobs import seed_categories
from app.workers.scheduler import start_scheduler, stop_scheduler

configure_logging(settings.debug)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await connect_to_mongo()
    await seed_categories()
    start_scheduler()
    logger.info("app_started", environment=settings.environment)
    yield
    stop_scheduler()
    await close_mongo_connection()
    logger.info("app_stopped")


app = FastAPI(
    title=settings.app_name,
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(repositories.router, prefix=settings.api_prefix)


@app.get("/health")
async def health():
    mongo = "unknown"
    try:
        from app.db.mongodb import get_db

        await get_db().command("ping")
        mongo = "up"
    except Exception:  # noqa: BLE001
        mongo = "down"
    return {
        "status": "ok" if mongo == "up" else "degraded",
        "service": settings.app_name,
        "mongo": mongo,
    }
