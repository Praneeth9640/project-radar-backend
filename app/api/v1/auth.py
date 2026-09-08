"""Auth endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import get_current_user, get_database
from app.core.security import create_access_token, hash_password, verify_password
from app.models.schemas import MessageOut, TokenResponse, UserCreate, UserLogin, UserOut
from app.repositories.data import UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
async def register(payload: UserCreate, db: AsyncIOMotorDatabase = Depends(get_database)):
    users = UserRepository(db)
    existing = await users.get_by_email(payload.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = await users.create(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    token = create_access_token(user["id"], extra={"email": user["email"]})
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(payload: UserLogin, db: AsyncIOMotorDatabase = Depends(get_database)):
    users = UserRepository(db)
    user = await users.get_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(user["id"], extra={"email": user["email"]})
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
async def me(user=Depends(get_current_user)):
    return UserOut(
        id=user["id"],
        email=user["email"],
        display_name=user.get("display_name"),
        created_at=user["created_at"],
    )


@router.get("/health-auth", response_model=MessageOut)
async def health_auth():
    return MessageOut(message="auth ok")
