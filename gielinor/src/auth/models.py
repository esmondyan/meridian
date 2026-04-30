"""
User and analytics database models.

Uses a separate SQLite file (app.db) from price data.
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from config.settings import DATA_DIR

APP_DB_PATH = Path(DATA_DIR) / "app.db"


def get_conn() -> sqlite3.Connection:
    APP_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(APP_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db():
    """Create users + analytics tables (idempotent)."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT NOT NULL UNIQUE,
                username    TEXT NOT NULL,
                password    TEXT NOT NULL,  -- bcrypt hash
                role        TEXT NOT NULL DEFAULT 'free',  -- free | pro | admin
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                last_login  TEXT,
                is_active   INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

            CREATE TABLE IF NOT EXISTS analytics_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,  -- NULL for anonymous
                event_type  TEXT NOT NULL,  -- page_view, search, click_score, etc.
                event_data  TEXT,  -- JSON blob (page, item_id, filters, etc.)
                ip_address  TEXT,
                user_agent  TEXT,
                session_id  TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_events_type ON analytics_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_user ON analytics_events(user_id);
            CREATE INDEX IF NOT EXISTS idx_events_created ON analytics_events(created_at);

            CREATE TABLE IF NOT EXISTS subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                plan        TEXT NOT NULL,  -- pro | pro_discord
                status      TEXT NOT NULL DEFAULT 'active',  -- active | cancelled | expired
                started_at  TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at  TEXT,
                payment_ref TEXT  -- future: Stripe/PayPal ref
            );

            CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id);
        """)


# ── User CRUD ──────────────────────────────────────────────────────

def create_user(email: str, username: str, password_hash: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, username, password) VALUES (?, ?, ?)",
            (email, username, password_hash),
        )
        return cur.lastrowid


def get_user_by_email(email: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def update_last_login(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET last_login = datetime('now') WHERE id = ?",
            (user_id,),
        )


def update_user_role(user_id: int, role: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET role = ? WHERE id = ?", (role, user_id)
        )


# ── Analytics Events ───────────────────────────────────────────────

def log_event(
    event_type: str,
    event_data: Optional[str] = None,
    user_id: Optional[int] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    session_id: Optional[str] = None,
):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO analytics_events
               (user_id, event_type, event_data, ip_address, user_agent, session_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, event_type, event_data, ip_address, user_agent, session_id),
        )


# ── Analytics Queries ──────────────────────────────────────────────

def get_event_counts(since_days: int = 7) -> list[dict]:
    """Event type breakdown."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT event_type, COUNT(*) as count
               FROM analytics_events
               WHERE created_at >= datetime('now', ?)
               GROUP BY event_type
               ORDER BY count DESC""",
            (f"-{since_days} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def get_daily_active_users(since_days: int = 14) -> list[dict]:
    """Daily unique users (by user_id or session_id)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT DATE(created_at) as day,
                      COUNT(DISTINCT COALESCE(user_id, session_id)) as dau
               FROM analytics_events
               WHERE created_at >= datetime('now', ?)
               GROUP BY day
               ORDER BY day""",
            (f"-{since_days} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def get_user_registrations(since_days: int = 30) -> list[dict]:
    """New user registrations per day."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT DATE(created_at) as day, COUNT(*) as count
               FROM users
               WHERE created_at >= datetime('now', ?)
               GROUP BY day
               ORDER BY day""",
            (f"-{since_days} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def get_total_users() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        free = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='free'"
        ).fetchone()[0]
        pro = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='pro' OR role='admin'"
        ).fetchone()[0]
        return {"total": total, "free": free, "pro": pro}


def get_total_events(since_days: int = 7) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM analytics_events WHERE created_at >= datetime('now', ?)",
            (f"-{since_days} days",),
        ).fetchone()[0]
