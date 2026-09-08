"""MongoDB connection and index bootstrap."""

import os
import socket

import certifi
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ServerSelectionTimeoutError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Ensure OpenSSL on cloud hosts (Render) trusts public CAs used by Atlas.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

_client: AsyncIOMotorClient | None = None


async def connect_to_mongo() -> AsyncIOMotorDatabase:
    global _client
    # Force IPv4 — some hosts fail Atlas TLS when preferring IPv6.
    _client = AsyncIOMotorClient(
        settings.mongodb_uri,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=20000,
        connectTimeoutMS=20000,
        family=socket.AF_INET,
    )
    db = _client[settings.mongodb_db_name]
    try:
        await db.command("ping")
    except ServerSelectionTimeoutError:
        logger.error(
            "mongodb_unreachable",
            hint=(
                "Atlas Network Access must allow Render. "
                "In Atlas → Network Access → Add IP Address → Allow Access from Anywhere (0.0.0.0/0)."
            ),
        )
        raise
    await ensure_indexes(db)
    logger.info("mongodb_connected", db=settings.mongodb_db_name)
    return db


async def close_mongo_connection() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
        logger.info("mongodb_disconnected")


def get_client() -> AsyncIOMotorClient:
    if _client is None:
        raise RuntimeError("MongoDB client is not initialized")
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[settings.mongodb_db_name]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.users.create_index("email", unique=True)
    await db.users.create_index("clerk_user_id", unique=True, sparse=True)
    await db.repositories.create_index("github_repository_id", unique=True)
    await db.repositories.create_index("full_name", unique=True)
    await db.repositories.create_index([("stars", -1)])
    await db.repositories.create_index([("created_at", -1)])
    await db.repositories.create_index([("discovered_at", -1)])
    await db.repositories.create_index([("language", 1), ("stars", -1)])
    await db.repositories.create_index([("topics", 1)])
    # language_override must not collide with repository.language (programming language)
    await db.repositories.create_index(
        [("name", "text"), ("description", "text"), ("full_name", "text")],
        default_language="none",
        language_override="unused_language_field",
    )

    await db.repository_snapshots.create_index(
        [("repository_id", 1), ("captured_at", -1)]
    )
    await db.repository_snapshots.create_index(
        [("github_repository_id", 1), ("captured_at", -1)]
    )

    await db.repository_scores.create_index("repository_id", unique=True)
    await db.repository_scores.create_index([("momentum_score", -1)])
    await db.repository_scores.create_index([("stars_gained_7d", -1)])
    await db.repository_scores.create_index([("stars_gained_24h", -1)])
    await db.repository_scores.create_index([("stars_gained_30d", -1)])
    await db.repository_scores.create_index([("labels", 1)])

    await db.categories.create_index("slug", unique=True)
    await db.repository_categories.create_index(
        [("repository_id", 1), ("category_id", 1)], unique=True
    )

    await db.saved_repositories.create_index(
        [("user_id", 1), ("repository_id", 1)], unique=True
    )
    await db.saved_repositories.create_index([("user_id", 1), ("tags", 1)])

    await db.user_notes.create_index(
        [("user_id", 1), ("repository_id", 1)], unique=True
    )

    await db.alerts.create_index([("user_id", 1), ("active", 1)])
    await db.alerts.create_index([("repository_id", 1), ("active", 1)])

    await db.ai_analyses.create_index(
        [("repository_id", 1), ("created_at", -1)]
    )

    await db.sync_jobs.create_index([("job_type", 1), ("started_at", -1)])
    logger.info("mongodb_indexes_ensured")
