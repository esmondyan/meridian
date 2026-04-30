"""
EVE Online ESI SSO — OAuth2 认证模块

支持两种流程：
1. 浏览器回调（Streamlit query params 自动截获 ?code=xxx）
2. 手动粘贴（用户从浏览器 URL 复制 authorization code）

CCP 要求非 localhost 的 callback 必须 HTTPS。我们服务器暂无域名证书，
所以默认使用「手动粘贴」模式，用户在 CCP 授权后从地址栏复制 code。
"""

import json
import time
import secrets
import hashlib
import base64
import urllib.parse
import sqlite3
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import requests

# ── Constants ──────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "eve_auth.db"

ESI_BASE     = "https://esi.evetech.net/latest"
LOGIN_BASE   = "https://login.eveonline.com/v2"   # ESI SSO v2
AUTH_URL     = f"{LOGIN_BASE}/oauth/authorize"
TOKEN_URL    = f"{LOGIN_BASE}/oauth/token"

# ESI scopes we need（最小权限原则）
DEFAULT_SCOPES = [
    "esi-markets.structure_markets.v1",    # 读取玩家建筑内市场订单
    "esi-markets.read_character_orders.v1",# 读取角色市场挂单
    "esi-wallet.read_character_wallet.v1", # 读取角色钱包余额
    "esi-characters.read_contacts.v1",     # 读取联系人（可用于通知）
]

# ── Data Models ────────────────────────────────────────────

@dataclass
class EveToken:
    """ESI 认证令牌"""
    character_id:   int
    character_name: str
    access_token:   str
    refresh_token:  str
    expires_at:     float           # Unix timestamp
    scopes:         list[str] = field(default_factory=list)
    token_type:     str = "Bearer"


@dataclass
class EveSSOConfig:
    """SSO 配置（从 .env / 环境变量加载）"""
    client_id:     str = ""
    client_secret: str = ""
    callback_url:  str = "http://localhost/"   # CCP 允许 localhost HTTP
    user_agent:    str = "Meridian-EVE/1.0 (contact: meridian@example.com)"
    scopes:        list[str] = field(default_factory=lambda: DEFAULT_SCOPES)


# ── SQLite Token Store ─────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _init_auth_db():
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sso_tokens (
                character_id   INTEGER PRIMARY KEY,
                character_name TEXT    NOT NULL,
                access_token   TEXT    NOT NULL,
                refresh_token  TEXT    NOT NULL,
                expires_at     REAL    NOT NULL,
                scopes         TEXT    NOT NULL,
                token_type     TEXT    DEFAULT 'Bearer',
                updated_at     TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS auth_state (
                state       TEXT PRIMARY KEY,
                code_verifier TEXT NOT NULL,
                created_at  REAL NOT NULL
            );
        """)
        conn.commit()

_init_auth_db()


# ── PKCE (Proof Key for Code Exchange) ─────────────────────

def _generate_pkce_pair() -> tuple[str, str]:
    """生成 PKCE code_verifier + code_challenge (S256)"""
    code_verifier = secrets.token_urlsafe(64)[:128]   # 43-128 chars
    sha256 = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(sha256).rstrip(b"=").decode()
    return code_verifier, code_challenge


def _save_state(state: str, code_verifier: str):
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO auth_state (state, code_verifier, created_at) VALUES (?, ?, ?)",
            (state, code_verifier, time.time()),
        )
        conn.commit()


def _pop_state(state: str) -> Optional[str]:
    """取出并删除 state，返回 code_verifier"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT code_verifier FROM auth_state WHERE state = ?", (state,)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM auth_state WHERE state = ?", (state,))
            conn.commit()
            return row["code_verifier"]
    return None


# ── Token Store ────────────────────────────────────────────

def save_token(token: EveToken):
    with _get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO sso_tokens
               (character_id, character_name, access_token, refresh_token,
                expires_at, scopes, token_type, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                token.character_id,
                token.character_name,
                token.access_token,
                token.refresh_token,
                token.expires_at,
                json.dumps(token.scopes),
                token.token_type,
            ),
        )
        conn.commit()


def load_token(character_id: int) -> Optional[EveToken]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sso_tokens WHERE character_id = ?", (character_id,)
        ).fetchone()
    if row:
        return EveToken(
            character_id=row["character_id"],
            character_name=row["character_name"],
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            expires_at=row["expires_at"],
            scopes=json.loads(row["scopes"]),
            token_type=row["token_type"],
        )
    return None


def list_tokens() -> list[EveToken]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sso_tokens ORDER BY updated_at DESC"
        ).fetchall()
    return [
        EveToken(
            character_id=r["character_id"],
            character_name=r["character_name"],
            access_token=r["access_token"],
            refresh_token=r["refresh_token"],
            expires_at=r["expires_at"],
            scopes=json.loads(r["scopes"]),
            token_type=r["token_type"],
        )
        for r in rows
    ]


def delete_token(character_id: int):
    with _get_conn() as conn:
        conn.execute("DELETE FROM sso_tokens WHERE character_id = ?", (character_id,))
        conn.commit()


# ── OAuth2 Flow ────────────────────────────────────────────

class EveSSO:
    """EVE Online ESI SSO 客户端"""

    def __init__(self, config: EveSSOConfig = None):
        self.config = config or EveSSOConfig()

    # ── Step 1: 生成授权 URL ─────────────────────────────

    def get_auth_url(self) -> tuple[str, str]:
        """
        返回 (authorization_url, state)
        用户需要在浏览器打开这个 URL，授权后拿到 code
        """
        state = secrets.token_urlsafe(32)
        code_verifier, code_challenge = _generate_pkce_pair()
        _save_state(state, code_verifier)

        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.callback_url,
            "scope": " ".join(self.config.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
        return url, state

    # ── Step 2: 用 code 换 token ─────────────────────────

    def exchange_code(self, code: str, expected_state: str = None) -> Optional[EveToken]:
        """
        用授权码交换 access_token。
        expected_state: 如果提供，验证 state 防止 CSRF
        """
        code_verifier = None
        if expected_state:
            code_verifier = _pop_state(expected_state)

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.config.client_id,
            "redirect_uri": self.config.callback_url,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier

        headers = {
            "User-Agent": self.config.user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        # HTTP Basic Auth (client_id:client_secret), only if secret is set
        if self.config.client_secret:
            creds = base64.b64encode(
                f"{self.config.client_id}:{self.config.client_secret}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {creds}"

        try:
            resp = requests.post(
                TOKEN_URL, data=payload, headers=headers, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"[SSO] Token exchange failed: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"[SSO] Response: {e.response.text[:500]}")
            return None

        # Parse tokens
        access_token  = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in    = data.get("expires_in", 1200)  # default 20 min

        if not access_token:
            print(f"[SSO] No access_token in response: {json.dumps(data, indent=2)[:500]}")
            return None

        # Get character info
        char_info = self._get_character_info(access_token)
        if not char_info:
            return None

        token = EveToken(
            character_id=char_info["CharacterID"],
            character_name=char_info["CharacterName"],
            access_token=access_token,
            refresh_token=refresh_token or "",
            expires_at=time.time() + expires_in,
            scopes=data.get("scopes", self.config.scopes),
        )

        save_token(token)
        return token

    # ── Token refresh ─────────────────────────────────────

    def refresh(self, character_id: int) -> Optional[EveToken]:
        """用 refresh_token 刷新过期的 access_token"""
        token = load_token(character_id)
        if not token or not token.refresh_token:
            return None

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": self.config.client_id,
        }
        headers = {
            "User-Agent": self.config.user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if self.config.client_secret:
            creds = base64.b64encode(
                f"{self.config.client_id}:{self.config.client_secret}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {creds}"

        try:
            resp = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"[SSO] Refresh failed: {e}")
            return None

        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token", token.refresh_token)
        expires_in = data.get("expires_in", 1200)

        if not access_token:
            return None

        new_token = EveToken(
            character_id=token.character_id,
            character_name=token.character_name,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
            scopes=token.scopes,
        )
        save_token(new_token)
        return new_token

    def get_valid_token(self, character_id: int) -> Optional[EveToken]:
        """获取有效 token，过期则自动刷新"""
        token = load_token(character_id)
        if not token:
            return None
        if time.time() > token.expires_at - 60:   # 提前 1 分钟刷新
            token = self.refresh(character_id)
        return token

    # ── Helpers ───────────────────────────────────────────

    def _get_character_info(self, access_token: str) -> Optional[dict]:
        """用 access_token 查角色信息"""
        try:
            resp = requests.get(
                f"{LOGIN_BASE}/oauth/verify",
                headers={
                    "User-Agent": self.config.user_agent,
                    "Authorization": f"Bearer {access_token}",
                },
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"[SSO] Character verify failed: {e}")
            return None

    # ── ESI Authenticated API ─────────────────────────────

    def esi_get(self, character_id: int, path: str) -> Optional[dict]:
        """调用 ESI 认证接口"""
        token = self.get_valid_token(character_id)
        if not token:
            return None
        try:
            resp = requests.get(
                f"{ESI_BASE}{path}",
                headers={
                    "User-Agent": self.config.user_agent,
                    "Authorization": f"Bearer {token.access_token}",
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"[SSO] ESI GET {path} failed: {e}")
            return None

    def esi_post(self, character_id: int, path: str, data: dict) -> Optional[dict]:
        """调用 ESI 认证接口（POST）"""
        token = self.get_valid_token(character_id)
        if not token:
            return None
        try:
            resp = requests.post(
                f"{ESI_BASE}{path}",
                json=data,
                headers={
                    "User-Agent": self.config.user_agent,
                    "Authorization": f"Bearer {token.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json() if resp.text else {}
        except requests.RequestException as e:
            print(f"[SSO] ESI POST {path} failed: {e}")
            return None
