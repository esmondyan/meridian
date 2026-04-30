"""
Streamlit auth UI components — login, register, profile sidebar.

Token is stored in st.session_state. Non-logged-in users can still
use all features (V0 behavior).
"""
from typing import Optional

import streamlit as st
import requests

from config.settings import API_PORT

API_BASE = f"http://127.0.0.1:{API_PORT}"


def _api_post(path: str, data: dict) -> Optional[dict]:
    try:
        resp = requests.post(
            f"{API_BASE}{path}", json=data, timeout=5
        )
        if resp.status_code == 200:
            return resp.json()
        return {"error": resp.json().get("detail", "Unknown error")}
    except requests.ConnectionError:
        return {"error": "API server not running"}
    except Exception as e:
        return {"error": str(e)}


def _api_get(path: str, token: Optional[str] = None) -> Optional[dict]:
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = requests.get(
            f"{API_BASE}{path}", headers=headers, timeout=5
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def init_auth_state():
    """Ensure session state keys exist."""
    if "token" not in st.session_state:
        st.session_state.token = None
    if "refresh_token" not in st.session_state:
        st.session_state.refresh_token = None
    if "user" not in st.session_state:
        st.session_state.user = None
    if "session_id" not in st.session_state:
        import uuid
        st.session_state.session_id = str(uuid.uuid4())


def render_sidebar_auth():
    """Render login/register/profile in the sidebar."""
    init_auth_state()

    with st.sidebar:
        st.markdown("### 👤 Account")

        if st.session_state.user:
            # ── Logged in ──
            user = st.session_state.user
            st.success(f"Logged in as **{user.get('username', user['email'])}**")
            st.caption(f"Role: {user.get('role', 'free')}")

            if st.button("🚪 Logout"):
                st.session_state.token = None
                st.session_state.refresh_token = None
                st.session_state.user = None
                st.rerun()

            # Admin link
            if user.get("role") == "admin":
                st.markdown("---")
                st.markdown("### 🔧 Admin")
                admin_page = st.checkbox("📊 Show Analytics", key="admin_show")
                if admin_page:
                    st.markdown("_Run `streamlit run src/dashboard/admin.py` for full analytics_")

        else:
            # ── Not logged in: show login/register toggle ──
            tab_login, tab_register = st.tabs(["Login", "Register"])

            with tab_login:
                email = st.text_input("Email", key="login_email")
                password = st.text_input("Password", type="password", key="login_pw")
                if st.button("Sign In", use_container_width=True):
                    result = _api_post("/api/auth/login", {
                        "email": email, "password": password
                    })
                    if result and "access_token" in result:
                        st.session_state.token = result["access_token"]
                        st.session_state.refresh_token = result.get("refresh_token")
                        st.session_state.user = {
                            "user_id": result["user_id"],
                            "username": result.get("username", email),
                            "email": result["email"],
                            "role": result.get("role", "free"),
                        }
                        st.rerun()
                    else:
                        err = result.get("error", "Login failed") if result else "API unavailable"
                        st.error(err)

            with tab_register:
                reg_email = st.text_input("Email", key="reg_email")
                reg_user = st.text_input("Username", key="reg_user")
                reg_pw = st.text_input("Password", type="password", key="reg_pw")
                reg_pw2 = st.text_input("Confirm Password", type="password", key="reg_pw2")
                if st.button("Create Account", use_container_width=True):
                    if reg_pw != reg_pw2:
                        st.error("Passwords don't match")
                    elif len(reg_pw) < 6:
                        st.error("Password must be at least 6 characters")
                    else:
                        result = _api_post("/api/auth/register", {
                            "email": reg_email,
                            "username": reg_user,
                            "password": reg_pw,
                        })
                        if result and "access_token" in result:
                            st.session_state.token = result["access_token"]
                            st.session_state.refresh_token = result.get("refresh_token")
                            st.session_state.user = {
                                "user_id": result["user_id"],
                                "username": reg_user,
                                "email": reg_email,
                                "role": result.get("role", "free"),
                            }
                            st.rerun()
                        else:
                            err = result.get("error", "Registration failed") if result else "API unavailable"
                            st.error(err)

            st.caption("No account needed — all features are available to everyone for now.")
