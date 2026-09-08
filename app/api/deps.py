"""FastAPI dependencies."""

from typing import Annotated, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.clerk_auth import verify_clerk_token
from app.core.security import decode_access_token
from app.db.mongodb import get_db
from app.repositories.data import UserRepository

security = HTTPBearer(auto_error=False)


async def get_database() -> AsyncIOMotorDatabase:
    return get_db()


async def get_current_user_optional(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
):
    if not credentials:
        return None

    token = credentials.credentials
    users = UserRepository(db)

    # Prefer Clerk session tokens (RS256 / iss contains clerk).
    clerk_claims = verify_clerk_token(token)
    if clerk_claims and clerk_claims.get("sub"):
        email = (
            clerk_claims.get("email")
            or clerk_claims.get("primary_email_address")
            or None
        )
        display_name = clerk_claims.get("name") or clerk_claims.get("username")
        return await users.upsert_from_clerk(
            clerk_user_id=clerk_claims["sub"],
            email=email,
            display_name=display_name,
        )

    # Legacy email/password JWT fallback.
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
    return await users.get_by_id(payload["sub"])


async def get_current_user(
    user=Depends(get_current_user_optional),
):
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user
