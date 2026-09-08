"""Data-access helpers for MongoDB collections."""

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase


def oid(value: str | ObjectId) -> ObjectId:
    return value if isinstance(value, ObjectId) else ObjectId(value)


def serialize_doc(doc: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not doc:
        return None
    out = dict(doc)
    if "_id" in out:
        out["id"] = str(out.pop("_id"))
    for key, value in list(out.items()):
        if isinstance(value, ObjectId):
            out[key] = str(value)
    return out


class BaseRepository:
    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str):
        self.db = db
        self.collection = db[collection_name]


class UserRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "users")

    async def create(self, email: str, password_hash: str, display_name: str | None):
        doc = {
            "email": email.lower(),
            "password_hash": password_hash,
            "display_name": display_name,
            "auth_provider": "password",
            "created_at": datetime.now(timezone.utc),
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return serialize_doc(doc)

    async def get_by_email(self, email: str):
        return serialize_doc(await self.collection.find_one({"email": email.lower()}))

    async def get_by_id(self, user_id: str):
        return serialize_doc(await self.collection.find_one({"_id": oid(user_id)}))

    async def get_by_clerk_id(self, clerk_user_id: str):
        return serialize_doc(
            await self.collection.find_one({"clerk_user_id": clerk_user_id})
        )

    async def upsert_from_clerk(
        self,
        clerk_user_id: str,
        email: str | None = None,
        display_name: str | None = None,
    ):
        now = datetime.now(timezone.utc)
        email_value = (email or f"{clerk_user_id}@clerk.users").lower()
        await self.collection.update_one(
            {"clerk_user_id": clerk_user_id},
            {
                "$set": {
                    "email": email_value,
                    "display_name": display_name,
                    "auth_provider": "clerk",
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "clerk_user_id": clerk_user_id,
                    "created_at": now,
                    "password_hash": None,
                },
            },
            upsert=True,
        )
        return await self.get_by_clerk_id(clerk_user_id)


class RepositoryRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "repositories")

    async def upsert_from_github(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        github_id = payload["github_repository_id"]
        update = {
            "$set": {**payload, "updated_in_app_at": now, "last_synced_at": now},
            "$setOnInsert": {"discovered_at": now},
        }
        await self.collection.update_one(
            {"github_repository_id": github_id}, update, upsert=True
        )
        doc = await self.collection.find_one({"github_repository_id": github_id})
        return serialize_doc(doc)  # type: ignore[return-value]

    async def get_by_id(self, repository_id: str):
        return serialize_doc(await self.collection.find_one({"_id": oid(repository_id)}))

    async def get_by_github_id(self, github_repository_id: int):
        return serialize_doc(
            await self.collection.find_one(
                {"github_repository_id": github_repository_id}
            )
        )

    async def list_ids(self, limit: int = 500) -> list[dict[str, Any]]:
        cursor = self.collection.find({}, {"_id": 1, "github_repository_id": 1, "full_name": 1}).limit(limit)
        return [serialize_doc(d) for d in await cursor.to_list(length=limit)]  # type: ignore[misc]

    async def search(
        self,
        *,
        q: str | None = None,
        language: str | None = None,
        topic: str | None = None,
        min_stars: int | None = None,
        max_age_days: int | None = None,
        min_momentum: float | None = None,
        label: str | None = None,
        sort: str = "momentum",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        match: dict[str, Any] = {}
        if q:
            match["$text"] = {"$search": q}
        if language:
            match["language"] = language
        if topic:
            match["topics"] = topic
        if min_stars is not None:
            match["stars"] = {"$gte": min_stars}
        if max_age_days is not None:
            cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
            match["created_at"] = {
                "$gte": datetime.fromtimestamp(cutoff, tz=timezone.utc)
            }

        score_match: dict[str, Any] = {}
        if min_momentum is not None:
            score_match["score.momentum_score"] = {"$gte": min_momentum}
        if label:
            score_match["score.labels"] = label

        sort_map = {
            "momentum": ("score.momentum_score", -1),
            "stars": ("stars", -1),
            "star_growth": ("score.stars_gained_7d", -1),
            "age": ("created_at", -1),
            "activity": ("pushed_at", -1),
        }
        sort_field, sort_dir = sort_map.get(sort, sort_map["momentum"])

        pipeline: list[dict[str, Any]] = [
            {"$match": match},
            {
                "$lookup": {
                    "from": "repository_scores",
                    "localField": "_id",
                    "foreignField": "repository_id",
                    "as": "score_docs",
                }
            },
            {
                "$addFields": {
                    "score": {"$arrayElemAt": ["$score_docs", 0]},
                }
            },
        ]
        if score_match:
            pipeline.append({"$match": score_match})

        count_pipeline = pipeline + [{"$count": "total"}]
        count_result = await self.collection.aggregate(count_pipeline).to_list(1)
        total = count_result[0]["total"] if count_result else 0

        pipeline.extend(
            [
                {"$sort": {sort_field: sort_dir, "_id": -1}},
                {"$skip": (page - 1) * page_size},
                {"$limit": page_size},
                {"$project": {"score_docs": 0}},
            ]
        )
        docs = await self.collection.aggregate(pipeline).to_list(page_size)
        return [self._flatten(d) for d in docs], total

    def _flatten(self, doc: dict[str, Any]) -> dict[str, Any]:
        score = doc.pop("score", None) or {}
        flat = serialize_doc(doc) or {}
        flat["momentum_score"] = score.get("momentum_score")
        flat["stars_gained_24h"] = score.get("stars_gained_24h")
        flat["stars_gained_7d"] = score.get("stars_gained_7d")
        flat["stars_gained_30d"] = score.get("stars_gained_30d")
        flat["stars_growth_pct_7d"] = score.get("stars_growth_pct_7d")
        flat["forks_gained_7d"] = score.get("forks_gained_7d")
        flat["labels"] = score.get("labels", [])
        created = flat.get("created_at")
        if isinstance(created, datetime):
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            flat["age_days"] = (
                datetime.now(timezone.utc) - created
            ).total_seconds() / 86400
        return flat


class SnapshotRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "repository_snapshots")

    async def insert_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        snapshot = {**snapshot, "captured_at": snapshot.get("captured_at") or datetime.now(timezone.utc)}
        if isinstance(snapshot.get("repository_id"), str):
            snapshot["repository_id"] = oid(snapshot["repository_id"])
        result = await self.collection.insert_one(snapshot)
        snapshot["_id"] = result.inserted_id
        return serialize_doc(snapshot)  # type: ignore[return-value]

    async def list_for_repository(self, repository_id: str, limit: int = 100):
        cursor = (
            self.collection.find({"repository_id": oid(repository_id)})
            .sort("captured_at", -1)
            .limit(limit)
        )
        docs = await cursor.to_list(limit)
        return [serialize_doc(d) for d in docs]

    async def get_nearest(
        self, repository_id: str, at_or_before: datetime
    ) -> Optional[dict[str, Any]]:
        doc = await self.collection.find_one(
            {
                "repository_id": oid(repository_id),
                "captured_at": {"$lte": at_or_before},
            },
            sort=[("captured_at", -1)],
        )
        return serialize_doc(doc)

    async def get_latest(self, repository_id: str) -> Optional[dict[str, Any]]:
        doc = await self.collection.find_one(
            {"repository_id": oid(repository_id)},
            sort=[("captured_at", -1)],
        )
        return serialize_doc(doc)


class ScoreRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "repository_scores")

    async def upsert(self, repository_id: str, payload: dict[str, Any]) -> None:
        await self.collection.update_one(
            {"repository_id": oid(repository_id)},
            {
                "$set": {
                    **payload,
                    "repository_id": oid(repository_id),
                    "calculated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )

    async def get(self, repository_id: str):
        return serialize_doc(
            await self.collection.find_one({"repository_id": oid(repository_id)})
        )


class SavedRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "saved_repositories")

    async def save(self, user_id: str, repository_id: str, tags: list[str]):
        now = datetime.now(timezone.utc)
        await self.collection.update_one(
            {"user_id": oid(user_id), "repository_id": oid(repository_id)},
            {
                "$set": {"tags": tags, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return serialize_doc(
            await self.collection.find_one(
                {"user_id": oid(user_id), "repository_id": oid(repository_id)}
            )
        )

    async def unsave(self, user_id: str, repository_id: str) -> bool:
        result = await self.collection.delete_one(
            {"user_id": oid(user_id), "repository_id": oid(repository_id)}
        )
        return result.deleted_count > 0

    async def list_for_user(self, user_id: str, q: str | None = None, tag: str | None = None):
        match: dict[str, Any] = {"user_id": oid(user_id)}
        if tag:
            match["tags"] = tag
        pipeline = [
            {"$match": match},
            {
                "$lookup": {
                    "from": "repositories",
                    "localField": "repository_id",
                    "foreignField": "_id",
                    "as": "repo",
                }
            },
            {"$unwind": "$repo"},
            {
                "$lookup": {
                    "from": "repository_scores",
                    "localField": "repository_id",
                    "foreignField": "repository_id",
                    "as": "score_docs",
                }
            },
            {"$addFields": {"score": {"$arrayElemAt": ["$score_docs", 0]}}},
        ]
        if q:
            pipeline.append(
                {
                    "$match": {
                        "$or": [
                            {"repo.full_name": {"$regex": q, "$options": "i"}},
                            {"repo.description": {"$regex": q, "$options": "i"}},
                        ]
                    }
                }
            )
        docs = await self.collection.aggregate(pipeline).to_list(200)
        results = []
        for d in docs:
            repo = d["repo"]
            score = d.get("score") or {}
            flat = serialize_doc(repo) or {}
            flat["momentum_score"] = score.get("momentum_score")
            flat["stars_gained_24h"] = score.get("stars_gained_24h")
            flat["stars_gained_7d"] = score.get("stars_gained_7d")
            flat["stars_gained_30d"] = score.get("stars_gained_30d")
            flat["labels"] = score.get("labels", [])
            flat["tags"] = d.get("tags", [])
            flat["saved"] = True
            results.append(flat)
        return results

    async def is_saved(self, user_id: str, repository_id: str) -> bool:
        doc = await self.collection.find_one(
            {"user_id": oid(user_id), "repository_id": oid(repository_id)}
        )
        return doc is not None

    async def saved_ids(self, user_id: str) -> set[str]:
        cursor = self.collection.find({"user_id": oid(user_id)}, {"repository_id": 1})
        docs = await cursor.to_list(1000)
        return {str(d["repository_id"]) for d in docs}


class NoteRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "user_notes")

    async def upsert(self, user_id: str, repository_id: str, content: str):
        now = datetime.now(timezone.utc)
        await self.collection.update_one(
            {"user_id": oid(user_id), "repository_id": oid(repository_id)},
            {
                "$set": {"content": content, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return serialize_doc(
            await self.collection.find_one(
                {"user_id": oid(user_id), "repository_id": oid(repository_id)}
            )
        )

    async def get(self, user_id: str, repository_id: str):
        return serialize_doc(
            await self.collection.find_one(
                {"user_id": oid(user_id), "repository_id": oid(repository_id)}
            )
        )


class AlertRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "alerts")

    async def create(self, user_id: str, data: dict[str, Any]):
        doc = {
            "user_id": oid(user_id),
            "repository_id": oid(data["repository_id"]),
            "condition_type": data["condition_type"],
            "threshold": data["threshold"],
            "label": data.get("label"),
            "active": True,
            "triggered": False,
            "triggered_at": None,
            "created_at": datetime.now(timezone.utc),
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return serialize_doc(doc)

    async def delete(self, user_id: str, alert_id: str) -> bool:
        result = await self.collection.delete_one(
            {"_id": oid(alert_id), "user_id": oid(user_id)}
        )
        return result.deleted_count > 0

    async def list_for_user(self, user_id: str):
        cursor = self.collection.find({"user_id": oid(user_id)}).sort("created_at", -1)
        return [serialize_doc(d) for d in await cursor.to_list(200)]

    async def list_active(self):
        cursor = self.collection.find({"active": True, "triggered": False})
        return [serialize_doc(d) for d in await cursor.to_list(1000)]

    async def mark_triggered(self, alert_id: str) -> None:
        await self.collection.update_one(
            {"_id": oid(alert_id)},
            {
                "$set": {
                    "triggered": True,
                    "triggered_at": datetime.now(timezone.utc),
                    "active": False,
                }
            },
        )


class AIAnalysisRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "ai_analyses")

    async def create(self, repository_id: str, summary: dict[str, Any], model: str):
        doc = {
            "repository_id": oid(repository_id),
            "summary": summary,
            "model": model,
            "created_at": datetime.now(timezone.utc),
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return serialize_doc(doc)

    async def latest(self, repository_id: str):
        return serialize_doc(
            await self.collection.find_one(
                {"repository_id": oid(repository_id)},
                sort=[("created_at", -1)],
            )
        )


class SyncJobRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db, "sync_jobs")

    async def start(self, job_type: str) -> str:
        doc = {
            "job_type": job_type,
            "status": "running",
            "started_at": datetime.now(timezone.utc),
            "finished_at": None,
            "message": None,
            "stats": {},
        }
        result = await self.collection.insert_one(doc)
        return str(result.inserted_id)

    async def finish(
        self,
        job_id: str,
        status: str,
        message: str | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        await self.collection.update_one(
            {"_id": oid(job_id)},
            {
                "$set": {
                    "status": status,
                    "finished_at": datetime.now(timezone.utc),
                    "message": message,
                    "stats": stats or {},
                }
            },
        )

    async def latest(self, job_type: str | None = None):
        query = {"job_type": job_type} if job_type else {}
        return serialize_doc(
            await self.collection.find_one(query, sort=[("started_at", -1)])
        )
