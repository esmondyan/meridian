"""
FastAPI server — auth endpoints + analytics event API.

Run: python -m src.api.server
"""
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

from config.settings import API_HOST, API_PORT
from src.auth.models import (
    init_auth_db, create_user, get_user_by_email, get_user_by_id,
    update_last_login, log_event,
    get_event_counts, get_daily_active_users,
    get_user_registrations, get_total_users, get_total_events,
)
from src.auth.handler import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    decode_token, refresh_access_token,
)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_auth_db()
    yield

app = FastAPI(title="OSRS Flipper API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas ────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str

class EventRequest(BaseModel):
    event_type: str
    event_data: Optional[dict] = {}
    user_id: Optional[int] = None
    session_id: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────

def _get_current_user(request: Request) -> Optional[dict]:
    """Extract user from Authorization header (optional auth)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    payload = decode_token(auth[7:])
    if payload is None:
        return None
    return get_user_by_id(int(payload["sub"]))


# ── Auth Routes ────────────────────────────────────────────────────

@app.post("/api/auth/register")
def register(req: RegisterRequest, request: Request):
    if get_user_by_email(req.email):
        raise HTTPException(400, "Email already registered")
    pw_hash = hash_password(req.password)
    user_id = create_user(req.email, req.username, pw_hash)
    access = create_access_token(user_id, "free")
    refresh = create_refresh_token(user_id)
    log_event("register", user_id=user_id, ip_address=request.client.host)
    return {
        "user_id": user_id,
        "access_token": access,
        "refresh_token": refresh,
        "role": "free",
    }


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    user = get_user_by_email(req.email)
    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(401, "Invalid email or password")
    if not user["is_active"]:
        raise HTTPException(403, "Account disabled")
    update_last_login(user["id"])
    access = create_access_token(user["id"], user["role"])
    refresh = create_refresh_token(user["id"])
    log_event("login", user_id=user["id"], ip_address=request.client.host)
    return {
        "user_id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "access_token": access,
        "refresh_token": refresh,
    }


@app.post("/api/auth/refresh")
def refresh(req: RefreshRequest):
    new_access = refresh_access_token(req.refresh_token)
    if not new_access:
        raise HTTPException(401, "Invalid or expired refresh token")
    return {"access_token": new_access}


@app.get("/api/auth/me")
def me(request: Request):
    user = _get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {
        "user_id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "created_at": user["created_at"],
        "last_login": user["last_login"],
    }


@app.get("/api/auth/check")
def check_token(request: Request):
    """Lightweight check: returns role or anon."""
    user = _get_current_user(request)
    if user:
        return {"authenticated": True, "role": user["role"], "user_id": user["id"]}
    return {"authenticated": False, "role": None}


# ── Analytics Routes ───────────────────────────────────────────────

@app.post("/api/analytics/event")
def track_event(req: EventRequest, request: Request):
    log_event(
        event_type=req.event_type,
        event_data=str(req.event_data) if req.event_data else None,
        user_id=req.user_id,
        session_id=req.session_id,
        ip_address=request.client.host,
        user_agent=request.headers.get("User-Agent"),
    )
    return {"ok": True}


@app.get("/api/analytics/dashboard")
def analytics_dashboard(request: Request, days: int = 7):
    """Admin-only: aggregated analytics."""
    user = _get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return {
        "total_users": get_total_users(),
        "total_events_7d": get_total_events(7),
        "events_by_type": get_event_counts(days),
        "daily_active_users": get_daily_active_users(14),
        "registrations": get_user_registrations(30),
    }


# ── Health ─────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


# ── Run ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")
