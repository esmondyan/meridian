"""
Streamlit 看板 — OSRS 价格监控与评分系统

启动: streamlit run src/dashboard/app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import plotly.express as px
import pandas as pd
import uuid
from datetime import datetime

from src.analyzer.price_analysis import (
    load_osrs_prices, get_latest_prices,
    get_price_trend, calc_spread_ratio
)
from src.storage.db import get_stats, get_latest_prices_df
from src.analytics.tracker import page_view, item_search, score_view, price_trend_view


st.set_page_config(page_title="Meridian: Gielinor", layout="wide")
st.title("🌐 Meridian: Gielinor")
st.caption("Old School RuneScape · 市场价格监控与评分系统")

# ── Auth + Analytics ──────────────────────────────────────────────
from src.dashboard.auth_ui import render_sidebar_auth, init_auth_state
init_auth_state()
render_sidebar_auth()

@st.cache_data(ttl=300)
def _load_latest():
    """只加载最新行情（~4400行），概览统计用 SQL 聚合"""
    stats = get_stats()
    latest = get_latest_prices_df()
    if "spread_ratio" not in latest.columns:
        latest["spread_ratio"] = (latest["spread"].fillna(0) / latest["low"].replace(0, 1) * 100).clip(lower=0)
    return stats, latest

stats, latest = _load_latest()

if latest.empty:
    st.warning("还没有数据，先跑一下 `python -m src.collectors.osrs_ge`")
    st.stop()

# ========== 概览 ==========
st.subheader("📊 概览")

col1, col2, col3, col4 = st.columns(4)
col1.metric("追踪物品数", stats["total_items"])
col2.metric("总采样数", stats["total_rows"])
col3.metric("最新采集时间", stats["latest"][:16] if isinstance(stats["latest"], str) else stats["latest"])

# 计算交易机会
candidates = latest[
    (latest["high"] >= 100_000) & (latest["high"] <= 5_000_000) &
    (latest["spread_ratio"] >= 2)
]
col4.metric("🎯 交易机会", len(candidates))

# ========== 最新价格表 ==========
st.subheader("📋 最新价格一览")
latest = latest.copy()

# 搜索过滤
search = st.text_input("🔍 搜索物品名称", "")
if search:
    latest = latest[latest["name"].str.contains(search, case=False)]
    # Analytics
    user_id = (st.session_state.get("user") or {}).get("user_id")
    item_search(search, len(latest), user_id=user_id, session_id=st.session_state.get("session_id"))

st.dataframe(
    latest[["name", "low", "high", "spread", "spread_ratio", "timestamp"]].sort_values("name"),
    use_container_width=True,
    hide_index=True,
    column_config={
        "name": "物品名称",
        "low": st.column_config.NumberColumn("买入价", format="%d"),
        "high": st.column_config.NumberColumn("卖出价", format="%d"),
        "spread": st.column_config.NumberColumn("差价", format="%d"),
        "spread_ratio": st.column_config.NumberColumn("价差比", format="%.2f%%"),
        "timestamp": "时间",
    },
)

# ========== 策略推荐 ==========
st.markdown("---")
st.subheader("💡 策略推荐")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🔥 中档热门", "💎 高端装备", "⚡ 快消小件", "💰 自定义筛选", "🎲 评分模型"
])

# ---------- 中档热门 ----------
with tab1:
    st.markdown("**中档热门（10万 ~ 500万 GP，价差比 ≥ 2%）** — 流动好，利润稳")
    mid = latest[
        (latest["high"] >= 100_000) &
        (latest["high"] <= 5_000_000) &
        (latest["spread_ratio"] >= 2)
    ].sort_values("spread", ascending=False)

    col_a, col_b = st.columns([1, 4])
    mid_top_n = col_a.selectbox("显示条数", [10, 20, 50, 100], key="mid_n", index=1)
    col_b.write("")  # 占位

    st.dataframe(
        mid.head(mid_top_n)[["name", "low", "high", "spread", "spread_ratio"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "name": "物品名称",
            "low": st.column_config.NumberColumn("买入价", format="%d"),
            "high": st.column_config.NumberColumn("卖出价", format="%d"),
            "spread": st.column_config.NumberColumn("差价", format="%d"),
            "spread_ratio": st.column_config.NumberColumn("价差比", format="%.2f%%"),
        },
    )

    # 中档热门价差分布图
    fig_mid = px.scatter(
        mid.head(50), x="high", y="spread", text="name",
        title="中档热门 — 价差 vs 价格",
        labels={"high": "价格 (GP)", "spread": "差价 (GP)"},
    )
    fig_mid.update_traces(textposition="top center", marker=dict(size=10))
    st.plotly_chart(fig_mid, use_container_width=True)

# ---------- 高端装备 ----------
with tab2:
    st.markdown("**高端装备（500万 GP 以上，价差比 ≥ 1%）** — 单笔利润大，但需要本钱，出手稍慢")
    hi = latest[
        (latest["high"] > 5_000_000) &
        (latest["spread_ratio"] >= 1)
    ].sort_values("spread", ascending=False)

    col_a, col_b = st.columns([1, 4])
    hi_top_n = col_a.selectbox("显示条数", [10, 20, 50], key="hi_n", index=1)
    col_b.write("")

    st.dataframe(
        hi.head(hi_top_n)[["name", "low", "high", "spread", "spread_ratio"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "name": "物品名称",
            "low": st.column_config.NumberColumn("买入价", format="%d"),
            "high": st.column_config.NumberColumn("卖出价", format="%d"),
            "spread": st.column_config.NumberColumn("差价", format="%d"),
            "spread_ratio": st.column_config.NumberColumn("价差比", format="%.2f%%"),
        },
    )

    # 高端价差分布
    fig_hi = px.bar(
        hi.head(20), x="name", y="spread",
        title="高端装备 Top 20 — 差价排行",
        labels={"spread": "差价 (GP)", "name": ""},
    )
    fig_hi.update_layout(xaxis_tickangle=-45)
    st.plotly_chart(fig_hi, use_container_width=True)

# ---------- 快消小件 ----------
with tab3:
    st.markdown("**快消小件（10万 GP 以下，价差比 ≥ 5%）** — ⚠️ 价差比高但很多是冷门物品，实际流动差")
    lo = latest[
        (latest["high"] < 100_000) &
        (latest["spread_ratio"] >= 5)
    ].sort_values("spread_ratio", ascending=False)

    col_a, col_b = st.columns([1, 4])
    lo_top_n = col_a.selectbox("显示条数", [10, 20, 50], key="lo_n", index=1)
    col_b.write("")

    st.dataframe(
        lo.head(lo_top_n)[["name", "low", "high", "spread", "spread_ratio"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "name": "物品名称",
            "low": st.column_config.NumberColumn("买入价", format="%d"),
            "high": st.column_config.NumberColumn("卖出价", format="%d"),
            "spread": st.column_config.NumberColumn("差价", format="%d"),
            "spread_ratio": st.column_config.NumberColumn("价差比", format="%.2f%%"),
        },
    )

    st.info("💡 提示：这些物品价差比看着很高，但很多是冷门/低流通物品，实际挂单可能要等很久才能出手。新手建议从中档热门开始。")

# ---------- 自定义筛选 ----------
with tab4:
    st.markdown("**自定义筛选器**")

    col_min, col_max, col_ratio = st.columns(3)
    min_price = col_min.number_input("最低价格", min_value=0, value=0, step=1000)
    max_price = col_max.number_input("最高价格", min_value=1000, value=1_000_000_000, step=100000)
    min_spread_ratio = col_ratio.number_input(
        "最低价差比 (%)", min_value=0.0, value=1.0, step=0.5, format="%.1f"
    )

    custom = latest[
        (latest["high"] >= min_price) &
        (latest["high"] <= max_price) &
        (latest["spread_ratio"] >= min_spread_ratio)
    ].sort_values("spread", ascending=False)

    # 预算模拟
    col_c1, col_c2 = st.columns([1, 4])
    budget = col_c1.number_input("你的本金 (GP)", min_value=0, value=0, step=100000)
    col_c2.write("")

    if budget > 0:
        affordable = custom[custom["high"] <= budget].sort_values("spread", ascending=False)
        st.markdown(f"**预算 {budget:,.0f} GP — 可买 {len(affordable)} 件物品**")
        display_df = affordable
    else:
        display_df = custom

    st.dataframe(
        display_df.head(50)[["name", "low", "high", "spread", "spread_ratio"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "name": "物品名称",
            "low": st.column_config.NumberColumn("买入价", format="%d"),
            "high": st.column_config.NumberColumn("卖出价", format="%d"),
            "spread": st.column_config.NumberColumn("差价", format="%d"),
            "spread_ratio": st.column_config.NumberColumn("价差比", format="%.2f%%"),
        },
    )

    if budget > 0 and not affordable.empty:
        st.success(
            f"💡 建议：用 {budget:,.0f} GP 买 **{affordable.iloc[0]['name']}** "
            f"（差价 {affordable.iloc[0]['spread']:,.0f} GP，价差比 {affordable.iloc[0]['spread_ratio']:.2f}%）"
        )

# ---------- 评分模型 ----------
with tab5:
    from src.analyzer.price_analysis import calc_flip_probability

    st.markdown(
        "**🎲 评分模型** — 综合流动性、利润率、物品种类，给每个物品打分（0~100）\n\n"
        "| 评级 | 含义 |\n"
        "|---|---|\n"
        "| ⭐ A+ (≥70) | 优质标的，流动好利润稳 |\n"
        "| 👍 A (55~70) | 不错的机会 |\n"
        "| 👌 B (40~55) | 中规中矩 |\n"
        "| ⚠️ C (25~40) | 高风险/低流动 |\n"
        "| ❌ D (<25) | 不建议碰 |\n"
    )

    flip_df = calc_flip_probability(latest)

    # 评级分布图
    grade_counts = flip_df["flip_grade"].value_counts().reindex(
        ["⭐ A+", "👍 A", "👌 B", "⚠️ C", "❌ D"]
    ).fillna(0)

    col_g1, col_g2 = st.columns([2, 3])
    with col_g1:
        fig_grade = px.pie(
            names=grade_counts.index,
            values=grade_counts.values,
            title="评级分布",
            color=grade_counts.index,
            color_discrete_map={
                "⭐ A+": "#00cc66", "👍 A": "#66cc00",
                "👌 B": "#ffcc00", "⚠️ C": "#ff8800", "❌ D": "#ff4444"
            },
        )
        st.plotly_chart(fig_grade, use_container_width=True)

    with col_g2:
        col_n, col_grade = st.columns(2)
        col_n.metric("⭐ A+ 推荐", int(grade_counts.get("⭐ A+", 0)))
        col_grade.metric("👍 A 良好", int(grade_counts.get("👍 A", 0)))

    # 筛选评分区间
    score_filter = st.select_slider(
        "最低评分", options=[0, 25, 40, 55, 70, 80, 90],
        value=55, format_func=lambda x: {0: "全部", 25: "≥ C", 40: "≥ B",
                                         55: "≥ A", 70: "≥ A+", 80: "≥ 80", 90: "≥ 90"}[x]
    )

    display_flip = flip_df[flip_df["flip_score"] >= score_filter]

    st.dataframe(
        display_flip.head(50)[
            ["name", "high", "spread", "spread_ratio",
             "volume_buy", "volume_sell",
             "buy_limit", "profit_after_tax", "roc", "daily_profit",
             "flip_score", "flip_grade"]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            "name": "物品名称",
            "high": st.column_config.NumberColumn("价格", format="%d"),
            "spread": st.column_config.NumberColumn("差价", format="%d"),
            "spread_ratio": st.column_config.NumberColumn("价差比", format="%.2f%%"),
            "volume_buy": st.column_config.NumberColumn("📈 买量/5min", format="%d"),
            "volume_sell": st.column_config.NumberColumn("📉 卖量/5min", format="%d"),
            "buy_limit": st.column_config.NumberColumn("限购/4h", format="%d"),
            "profit_after_tax": st.column_config.NumberColumn("税后利润", format="%d"),
            "roc": st.column_config.NumberColumn("🎯 RoC", format="%.2f%%"),
            "daily_profit": st.column_config.NumberColumn("日利润潜力", format="%d"),
            "flip_score": st.column_config.NumberColumn("评分", format="%.1f"),
            "flip_grade": "评级",
        },
    )

    # 高分物品的快照 → 按 RoC 排序（效率最高的在前）
    st.markdown("**🏆 最佳标的速览（A+ 按 RoC 排序）**")
    best = flip_df[
        (flip_df["flip_score"] >= 70) &
        (flip_df["profit_after_tax"] >= 50) &
        (flip_df["spread"] > 0) &
        (flip_df["roc"] > 0)
    ].nlargest(10, "roc")
    st.dataframe(
        best[["name", "high", "spread", "roc",
              "volume_buy", "volume_sell",
              "profit_after_tax", "daily_profit", "flip_score"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "name": "物品名称",
            "high": st.column_config.NumberColumn("价格", format="%d"),
            "spread": st.column_config.NumberColumn("差价", format="%d"),
            "roc": st.column_config.NumberColumn("🎯 资本回报率", format="%.2f%%"),
            "volume_buy": st.column_config.NumberColumn("📈 买量/5min", format="%d"),
            "volume_sell": st.column_config.NumberColumn("📉 卖量/5min", format="%d"),
            "profit_after_tax": st.column_config.NumberColumn("税后利润", format="%d"),
            "daily_profit": st.column_config.NumberColumn("日利润潜力", format="%d"),
            "flip_score": st.column_config.NumberColumn("评分", format="%.1f"),
        },
    )

    # 评分 vs 价格散点图
    fig_flip = px.scatter(
        flip_df[flip_df["flip_score"] >= 40].head(100),
        x="high", y="flip_score", color="flip_grade",
        text="name", hover_data=["spread", "spread_ratio"],
        title="评分 vs 价格（评分≥40，前100件）",
        labels={"high": "价格 (GP)", "flip_score": "评分"},
        color_discrete_map={
            "⭐ A+": "#00cc66", "👍 A": "#66cc00",
            "👌 B": "#ffcc00", "⚠️ C": "#ff8800", "❌ D": "#ff4444"
        },
    )
    fig_flip.update_traces(textposition="top center", textfont_size=9)
    st.plotly_chart(fig_flip, use_container_width=True)

# ========== 单品价格走势 ==========
st.markdown("---")
st.subheader("📈 单品价格走势")

item_names = sorted(latest["name"].unique())
selected = st.selectbox("选择物品查看走势", item_names)

# Analytics tracking
user_id = (st.session_state.get("user") or {}).get("user_id")
price_trend_view(selected, user_id=user_id, session_id=st.session_state.get("session_id"))

trend = get_price_trend(None, selected)
if not trend.empty:
    fig = px.line(
        trend,
        x="timestamp",
        y=["high", "low", "high_ma"],
        title=f"{selected} 价格走势",
        labels={"value": "价格 (GP)", "timestamp": "时间"},
    )
    st.plotly_chart(fig, use_container_width=True)
