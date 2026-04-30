"""
Password hashing + JWT token management.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from config.settings import (
    JWT_SECRET, JWT_ALGORITHM, JWT_ACCESS_EXPIRE_MINUTES,
    JWT_REFRESH_EXPIRE_DAYS,
)


# ── Password ───────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(
        password.encode("utf-8"), hashed.encode("utf-8")
    )


# ── JWT Tokens ─────────────────────────────────────────────────────

def create_access_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=JWT_ACCESS_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=JWT_REFRESH_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Returns payload dict or None if invalid/expired."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def refresh_access_token(refresh_token: str) -> Optional[str]:
    """Exchange a valid refresh token for a new access token."""
    payload = decode_token(refresh_token)
    if payload is None or payload.get("type") != "refresh":
        return None
    # Create new access token with same user + role
    # (role comes from DB on each login, not from token)
    from src.auth.models import get_user_by_id
    user = get_user_by_id(int(payload["sub"]))
    if user is None:
        return None
    return create_access_token(user["id"], user["role"])
