"""
Admin analytics dashboard — user stats, event tracking, usage metrics.
Auto-authenticates with local FastAPI server.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests

from config.settings import API_HOST, API_PORT

API_BASE = f"http://127.0.0.1:{API_PORT}"
ADMIN_EMAIL = "demo@demo.com"
ADMIN_PASS = "demo123"


def _get_token() -> str | None:
    """Auto-login with admin account, return access token."""
    try:
        r = requests.post(
            f"{API_BASE}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()["access_token"]
    except Exception:
        pass
    return None


# In-memory token cache (no session_state needed for admin-only page)
_AUTH_TOKEN = None


def _auth_fetch(endpoint: str):
    """Authenticated fetch from FastAPI."""
    global _AUTH_TOKEN
    if not _AUTH_TOKEN:
        _AUTH_TOKEN = _get_token()
    try:
        resp = requests.get(
            f"{API_BASE}{endpoint}",
            headers={"Authorization": f"Bearer {_AUTH_TOKEN}"},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
        # Token expired? Try re-login once
        if resp.status_code == 401 or resp.status_code == 403:
            _AUTH_TOKEN = _get_token()
            if _AUTH_TOKEN:
                resp = requests.get(
                    f"{API_BASE}{endpoint}",
                    headers={"Authorization": f"Bearer {_AUTH_TOKEN}"},
                    timeout=5,
                )
                if resp.status_code == 200:
                    return resp.json()
    except Exception:
        pass
    return None


def main():
    st.set_page_config(page_title="📊 Admin Analytics", layout="wide")
    st.title("📊 运行分析 · Admin Dashboard")

    # ── Quick health check ──
    health = _auth_fetch("/api/health")
    if not health:
        st.warning("⚠️ FastAPI 服务未启动或无管理员账号。")
        st.info("请确保 API 运行且 demo@demo.com 账号为 admin 角色。")
        st.stop()

    # ── 统计周期 ──
    col1, col2, col3 = st.columns(3)
    with col1:
        days = st.selectbox("统计周期", [1, 3, 7, 14, 30], index=2)

    # ── Pull data ──
    data = _auth_fetch(f"/api/analytics/dashboard?days={days}")
    if not data:
        st.error("无法获取分析数据 — 确认账号为 admin 角色")
        st.stop()

    # ── Overview Cards ──
    st.subheader("📈 概览")
    users = data.get("total_users", {})
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("👤 总注册用户", users.get("total", 0))
    mc2.metric("🆓 免费", users.get("free", 0))
    mc3.metric("💎 Pro", users.get("pro", 0))
    mc4.metric("📡 事件数 (7d)", data.get("total_events_7d", 0))

    # ── Event type breakdown ──
    st.subheader("🎯 用户行为分布")
    events = data.get("events_by_type", [])
    if events:
        df_events = pd.DataFrame(events)
        fig = px.pie(
            df_events, names="event_type", values="count",
            title=f"事件类型分布（近{days}天）",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("暂无事件数据 — 用户交互后会开始记录")

    # ── Daily Active Users ──
    st.subheader("📅 日活跃用户")
    dau = data.get("daily_active_users", [])
    if dau:
        df_dau = pd.DataFrame(dau)
        fig = px.line(
            df_dau, x="day", y="dau",
            title="Daily Active Users (14天)",
            markers=True,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("暂无日活数据")

    # ── Registrations ──
    st.subheader("🎉 新注册用户")
    regs = data.get("registrations", [])
    if regs:
        df_regs = pd.DataFrame(regs)
        fig = px.bar(
            df_regs, x="day", y="count",
            title="每日注册量（30天）",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("暂无注册数据 — 目前只有 demo 账号")

    # ── Raw table of events ──
    st.subheader("📋 原始事件记录")
    st.caption("可通过 API 扩展获取详细事件列表（后续版本）")


if __name__ == "__main__":
    main()
