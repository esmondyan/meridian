"""EVE Online 多站市场看板 + 交易委员会（Streamlit）

展示市场数据、跨区套利、多站物品详情对比 + 交易引擎多模型评审
"""

import sys
import os
from pathlib import Path

import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent))
from trade_engine.strategy import StrategyEngine, StrategyConfig
from trade_engine.proposal import TradeProposal
from trade_engine.evaluator import run_committee, create_standard_committee
from trade_engine.consensus import ConsensusEngine
from eve_storage import get_latest, get_stats, get_cross_region_arb, get_item_across_regions

from auth.eve_sso import EveSSO, EveSSOConfig, list_tokens, delete_token

DATA_DIR = Path(__file__).parent / "data"
REGION_LABELS = {
    "jita": "Jita", "amarr": "Amarr", "dodixie": "Dodixie",
    "rens": "Rens", "hek": "Hek",
}


def fmt(v):
    """格式化 ISK 数值显示"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.2f}B"
    elif abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M"
    elif abs(v) >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{v:.0f}"


# ============================
# 页面配置
# ============================
st.set_page_config(
    page_title="Meridian: New Eden",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] { gap: 2px; }
    .stTabs [data-baseweb="tab"] { padding: 8px 18px; font-weight: 500; }
    .block-container { padding-top: 1.5rem; padding-bottom: 1.5rem; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; }
</style>
""", unsafe_allow_html=True)

st.title("🚀 Meridian: New Eden")
st.caption("EVE Online · 跨枢纽自动化交易平台")


# ============================
# 数据加载（缓存）
# ============================
@st.cache_data(ttl=300)
def load_station_data(region: str = "jita"):
    df = get_latest(region=region, limit=9999)
    if df is not None and len(df) > 0:
        num_cols = ["bid_ask_spread", "spread_ratio", "station_score",
                     "best_buy_vol", "best_sell_vol", "gross_margin",
                     "best_buy", "best_sell"]
        for c in num_cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=300)
def load_arb_data():
    """即时套利（Jita 站内买卖倒挂）"""
    arb_path = DATA_DIR / "jita_full.csv"
    if not arb_path.exists():
        return None
    df = pd.read_csv(arb_path)
    for c in ["best_buy", "best_sell", "spread", "profit_after_tax",
               "spread_ratio", "best_buy_vol", "best_sell_vol", "eve_score"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=300)
def load_stats():
    return get_stats()


# ============================
# 侧边栏
# ============================
with st.sidebar:
    st.markdown("### 🚀 Meridian")
    stats = load_stats()
    if stats and stats.get("total_rows", 0) > 0:
        st.caption(
            f"📦 {stats['total_items']:,} 种物品 · "
            f"{stats['total_regions']} 区域 · "
            f"{stats['total_rows']:,} 行"
        )
        st.caption(f"⏱ 最新: {stats['latest'][:16]}")
    else:
        st.warning("暂无数据，运行采集脚本")

    st.divider()

    # ====================================
    # ESI SSO 登录
    # ====================================
    sso_config = EveSSOConfig()
    if "sso_client" not in st.session_state:
        # 尝试从环境变量读取
        import os
        client_id = os.environ.get("EVE_SSO_CLIENT_ID", "")
        client_secret = os.environ.get("EVE_SSO_CLIENT_SECRET", "")
        callback = os.environ.get("EVE_SSO_CALLBACK", "http://localhost/")
        sso_config.client_id = client_id
        sso_config.client_secret = client_secret
        sso_config.callback_url = callback
        st.session_state["sso_client"] = EveSSO(sso_config) if client_id else None
        st.session_state["sso_auth_url"] = None
        st.session_state["sso_state"] = None
        st.session_state["sso_logged_in_as"] = None

    sso = st.session_state["sso_client"]

    st.markdown("#### 🔐 EVE 账号")

    # 检查 URL 中的回调 code
    query_params = st.query_params
    if "code" in query_params and sso:
        code = query_params["code"]
        state = query_params.get("state", "")
        st.info("🔑 检测到授权码，正在交换 token...")
        token = sso.exchange_code(code, expected_state=state)
        if token:
            st.session_state["sso_logged_in_as"] = token.character_name
            st.rerun()
        else:
            st.error("Token 交换失败，请重试")

    # 已登录状态
    if st.session_state["sso_logged_in_as"]:
        st.success(f"👤 {st.session_state['sso_logged_in_as']}")
        st.caption("已连接 ESI")
        if st.button("🚪 断开", use_container_width=True, key="sso_logout"):
            tokens = list_tokens()
            for t in tokens:
                delete_token(t.character_id)
            st.session_state["sso_logged_in_as"] = None
            st.rerun()
    else:
        if sso is None:
            # 未配置 SSO — 显示手动输入
            st.caption("未检测到 SSO 配置")
            with st.expander("⚙️ 配置 SSO"):
                cid = st.text_input("Client ID", type="password", key="sso_cid")
                csec = st.text_input("Client Secret", type="password", key="sso_csec")
                cb = st.text_input("Callback URL", value="http://localhost/", key="sso_cb")
                if st.button("💾 保存配置", key="sso_save_cfg"):
                    if cid:
                        sso_config.client_id = cid
                        sso_config.client_secret = csec
                        sso_config.callback_url = cb
                        st.session_state["sso_client"] = EveSSO(sso_config)
                        st.rerun()
                    else:
                        st.error("至少需要 Client ID")
            st.info(
                "**如何获取凭据？**\n\n"
                "1. 访问 [developers.eveonline.com](https://developers.eveonline.com/)\n"
                "2. 创建新应用 → Authentication & API Access\n"
                "3. 添加 Permissions（至少勾选 esi-markets.*, esi-wallet.*）\n"
                "4. Callback URL 填 `http://localhost/`\n"
                "5. 复制 Client ID 和 Secret Key"
            )
        else:
            # 手动粘贴 code 模式
            st.caption("两种登录方式：")
            tab1, tab2 = st.tabs(["📋 手动粘贴", "🔗 浏览器回调"])

            with tab1:
                if st.button("🔑 生成授权链接", key="sso_gen_link"):
                    url, state = sso.get_auth_url()
                    st.session_state["sso_auth_url"] = url
                    st.session_state["sso_state"] = state

                if st.session_state["sso_auth_url"]:
                    st.text_area("📎 复制此链接在浏览器打开",
                                 st.session_state["sso_auth_url"], height=68,
                                 key="sso_link_display")
                    st.caption("授权后在浏览器地址栏复制 `?code=` 后面的值")
                    manual_code = st.text_input("粘贴授权码", key="sso_manual_code")
                    if st.button("✔️ 验证", key="sso_verify") and manual_code:
                        token = sso.exchange_code(
                            manual_code,
                            expected_state=st.session_state["sso_state"],
                        )
                        if token:
                            st.session_state["sso_logged_in_as"] = token.character_name
                            st.rerun()
                        else:
                            st.error("验证失败，授权码可能已过期")

            with tab2:
                st.caption("浏览器回调（需配好 Callback URL）")
                if st.button("🌐 EVE Online 登录", key="sso_redirect"):
                    url, state = sso.get_auth_url()
                    st.session_state["sso_state"] = state
                    st.markdown(f"[点击跳转 EVE 授权]({url})")

    st.divider()
    mode = st.radio("📋 模式", ["市场浏览", "交易引擎"], index=0)

    # ====================================


# ============================
# 模式 1：市场浏览
# ============================
if mode == "市场浏览":
    all_regions = [r["region"] for r in (stats or {}).get("regions", [])]
    if not all_regions:
        all_regions = ["jita"]

    tab1, tab2, tab3, tab4 = st.tabs([
        "🏪 站内挂单", "⚡ 即时套利", "🔄 跨区套利", "🔍 物品详情",
    ])

    # --- Tab 1：站内挂单 ---
    with tab1:
        st.subheader("🏪 站内挂单标的")
        st.caption("价差 > 0（卖价 > 买价），适合挂买单等卖、挂卖单等买")

        region = st.selectbox(
            "📍 选择星域",
            all_regions,
            format_func=lambda r: REGION_LABELS.get(r, r.capitalize()),
            key="tab1_region",
        )

        df = load_station_data(region)
        if df is not None and len(df) > 0:
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                min_score = st.slider("最低评分", 0, 100, 60, key="s_min_score")
            with col2:
                min_depth = st.number_input("最低深度(单侧)", 0, 100000, 10, key="s_min_depth")
            with col3:
                min_spread = st.number_input("最低价差比(%)", 0.0, 100.0, 3.0, key="s_min_spread")
            with col4:
                search = st.text_input("搜索物品", "", key="s_search")

            mask = (
                (df["station_score"] >= min_score)
                & (df["best_buy_vol"] >= min_depth)
                & (df["best_sell_vol"] >= min_depth)
                & (df["spread_ratio"] >= min_spread)
                & (df["bid_ask_spread"] > 0)
            )
            if search:
                mask &= df["name"].str.contains(search, case=False, na=False)

            filtered = df[mask].sort_values("station_score", ascending=False)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("🎯 标的数", len(filtered))
            c2.metric("💰 平均价差比",
                      f"{filtered['spread_ratio'].mean():.1f}%" if len(filtered) > 0 else "-")
            c3.metric("📦 平均深度",
                      f"{filtered['best_buy_vol'].mean():.0f}" if len(filtered) > 0 else "-")
            c4.metric("⭐ 平均评分",
                      f"{filtered['station_score'].mean():.1f}" if len(filtered) > 0 else "-")

            display = filtered[["name", "best_sell", "best_buy", "bid_ask_spread",
                                 "spread_ratio", "best_buy_vol", "best_sell_vol",
                                 "buy_order_count", "sell_order_count",
                                 "station_score"]].head(100).copy()
            display.columns = ["物品", "卖价", "买价", "价差", "价差比",
                               "买深度", "卖深度", "买单数", "卖单数", "评分"]
            for c in ["卖价", "买价", "价差"]:
                if c in display.columns:
                    display[c] = display[c].apply(
                        lambda x: f"{x:,.0f}" if pd.notna(x) else "-"
                    )
            display["价差比"] = display["价差比"].apply(
                lambda x: f"{x:.1f}%" if pd.notna(x) else "-"
            )

            st.dataframe(display, use_container_width=True, height=500)
        else:
            st.info(f"「{REGION_LABELS.get(region, region.capitalize())}」暂无数据")

    # --- Tab 2：即时套利（Jita 买卖倒挂）---
    with tab2:
        st.subheader("⚡ 即时套利")
        st.caption("买单 > 卖价，可直接买低卖高赚差价（Jita 站内）")

        arb_df = load_arb_data()
        if arb_df is not None and len(arb_df) > 0:
            col1, col2 = st.columns(2)
            with col1:
                min_arb_score = st.slider("最低评分", 0, 100, 40, key="a_min_score")
            with col2:
                min_arb_profit = st.number_input(
                    "最低利润(ISK)", 0, 10_000_000, 1_000, key="a_min_profit"
                )

            mask = (arb_df["eve_score"] >= min_arb_score) & (
                arb_df["profit_after_tax"] >= min_arb_profit
            )
            filtered = arb_df[mask].sort_values("eve_score", ascending=False)

            c1, c2, c3 = st.columns(3)
            c1.metric("🎯 机会数", len(filtered))
            c2.metric("💰 最高利润",
                      fmt(filtered["profit_after_tax"].max()) if len(filtered) > 0 else "0")
            c3.metric("📈 平均评分",
                      f"{filtered['eve_score'].mean():.1f}" if len(filtered) > 0 else "0")

            display = filtered[["name", "best_sell", "best_buy", "spread",
                                 "spread_ratio", "profit_after_tax",
                                 "eve_score"]].head(100).copy()
            display.columns = ["物品", "最佳卖价", "最佳买价", "差价",
                               "价差比", "税后利润", "评分"]
            for c in ["最佳卖价", "最佳买价", "差价", "税后利润"]:
                if c in display.columns:
                    display[c] = display[c].apply(
                        lambda x: f"{x:,.0f}" if pd.notna(x) else "-"
                    )
            display["价差比"] = display["价差比"].apply(
                lambda x: f"{x:.1f}%" if pd.notna(x) else "-"
            )

            st.dataframe(display, use_container_width=True, height=500)
        else:
            st.info("即时套利数据未采集（需要 jita_full.csv）")

    # --- Tab 3：跨区套利 ---
    with tab3:
        st.subheader("🔄 跨区套利 · 运输交易")
        st.caption("A区按卖价直接买入 → 运到B区按买价直接卖出，立等成交（运费后毛利）")

        # 运费 & 深度说明
        with st.expander("💵 运费估算 & ⚠️ 风险说明", expanded=True):
            co1, co2 = st.columns([3, 2])
            with co1:
                freight_pct = st.slider(
                    "估运费（占买价%）",
                    0.0, 20.0, 3.0, 0.5,
                    key="freight_pct",
                    help="跨区运输成本估算。推荐找 Red Frog / Push Industries 等货运公司\n"
                         "高安运费约 500-1000 ISK/m³/跳，这里用百分比估算方便快速筛选",
                )
                st.caption(
                    "🏁 **路线跳数（高安）**　"
                    "Jita↔Hek: ~17　|　Jita↔Amarr: ~12　|　"
                    "Hek↔Rens: ~3　|　Amarr↔Dodixie: ~9"
                )
            with co2:
                st.markdown("**📖 深度 = 限购**")
                st.caption(
                    "**买深度** = 当前卖单价能买多少件不涨价\n"
                    "**卖深度** = 当前买单价能卖多少件不降价\n\n"
                    "**可交易量** = min(买深度, 卖深度)\n"
                    "超过这个数要吃更贵的价，利润变薄"
                )
                st.markdown("**⚠️ 站内挂单 vs 跨区运输**")
                st.caption(
                    "🏪 **站内**：价格波动风险，无运输风险\n"
                    "🔄 **跨区**：多一层运输风险（货损/被炸），"
                    "但利润空间更大"
                )

        col1, col2, col3 = st.columns(3)
        with col1:
            min_profit = st.number_input(
                "最低即时利润(ISK)", 0, 1_000_000_000, 1_000,
                step=10_000, key="c_min_profit",
            )
        with col2:
            max_rows = st.slider("最大结果", 10, 200, 50, key="c_max")
        with col3:
            max_capital = st.number_input(
                "最多投入(ISK)", 0, 10_000_000_000, 500_000_000,
                step=10_000_000, key="c_max_capital",
                help="过滤掉资金需求过大的机会",
            )

        with st.spinner("查询跨区套利..."):
            arb = get_cross_region_arb(min_profit=min_profit, limit=max_rows)

        if arb is not None and len(arb) > 0:
            # 计算运费调整后的净利润
            arb["freight_cost"] = arb["buy_ask"] * freight_pct / 100
            arb["profit_after_freight"] = arb["profit_instant"] - arb["freight_cost"]
            arb["margin_after_freight_pct"] = arb["profit_after_freight"] / arb["buy_ask"] * 100
            arb["capital"] = arb["buy_ask"] * arb["max_tradeable"]

            # 过滤：运费后利润>0 + 资金门槛
            valid = arb[
                (arb["profit_after_freight"] > 0)
                & (arb["capital"] <= max_capital)
            ].copy()

            if len(valid) > 0:
                # 按运费后利润排序
                valid = valid.sort_values("profit_after_freight", ascending=False)

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("🎯 可行机会", len(valid))
                c2.metric("💰 最高净利", fmt(valid["profit_after_freight"].max()))
                c3.metric("📈 平均净利率",
                          f"{valid['margin_after_freight_pct'].mean():.1f}%")
                top = valid.iloc[0]
                c4.metric("🏆 最佳路线",
                          f"{REGION_LABELS.get(top['buy_region'], top['buy_region'].capitalize())}→"
                          f"{REGION_LABELS.get(top['sell_region'], top['sell_region'].capitalize())}")

                display = valid.copy()
                display["buy_region"] = display["buy_region"].apply(
                    lambda r: REGION_LABELS.get(r, r.capitalize())
                )
                display["sell_region"] = display["sell_region"].apply(
                    lambda r: REGION_LABELS.get(r, r.capitalize())
                )

                display_tbl = display[[
                    "name", "buy_region", "buy_ask", "sell_region", "sell_bid",
                    "margin_instant_pct", "profit_instant", "freight_cost",
                    "profit_after_freight", "max_tradeable", "capital",
                ]].head(100).copy()

                display_tbl.columns = [
                    "物品", "买入区", "买入价", "卖出区", "卖出价",
                    "利润率%", "税前利润", "运费估", "运费后净利",
                    "可交易(件)", "需资金",
                ]

                for c in ["买入价", "卖出价", "税前利润", "运费估", "运费后净利", "需资金"]:
                    display_tbl[c] = display_tbl[c].apply(
                        lambda x: f"{x:,.0f}" if pd.notna(x) else "-"
                    )
                display_tbl["利润率%"] = display_tbl["利润率%"].apply(
                    lambda x: f"{x:.1f}%" if pd.notna(x) else "-"
                )

                st.dataframe(display_tbl, use_container_width=True, height=500)
            else:
                st.info("符合条件的机会为 0，试试调低运费/资金门槛或最低利润")
        else:
            st.info(f"未找到 ≥{fmt(min_profit)} ISK 的跨区套利机会")

    # --- Tab 4：物品详情 ---
    with tab4:
        st.subheader("🔍 物品详情")
        st.caption("查看物品在全部区域的价格对比")

        if "detail_type_id" not in st.session_state:
            st.session_state.detail_type_id = None
        if "detail_lookups" not in st.session_state:
            st.session_state.detail_lookups = None

        search_term = st.text_input("搜索物品名称", "", key="detail_search")

        if search_term:
            item_df = get_item_across_regions(name_query=search_term)
            lookups = item_df.attrs.get("lookups", None)

            if lookups is not None and len(lookups) > 0:
                st.caption(f"匹配到 {len(lookups)} 个物品")

                if len(lookups) > 1:
                    selected_name = st.selectbox(
                        "选择具体物品",
                        lookups["name"].tolist(),
                        key="detail_item_select",
                    )
                    selected_row = lookups[lookups["name"] == selected_name].iloc[0]
                    st.session_state.detail_type_id = int(selected_row["type_id"])
                else:
                    st.session_state.detail_type_id = int(lookups.iloc[0]["type_id"])

            elif len(item_df) > 0:
                st.session_state.detail_type_id = int(item_df.iloc[0]["type_id"])
            else:
                st.info("未找到匹配的物品")
                st.session_state.detail_type_id = None

        # 显示跨站数据
        if st.session_state.detail_type_id is not None:
            cross = get_item_across_regions(
                type_id=st.session_state.detail_type_id
            )
            if cross is not None and len(cross) > 0:
                item_name = cross.iloc[0]["name"]

                # 指标卡
                total_value = (
                    cross["best_buy"].fillna(0)
                    * cross["best_buy_vol"].fillna(0)
                ).sum()
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("物品", item_name)
                c2.metric("覆盖区域", f"{len(cross)} / 5")
                c3.metric("全市场估值", fmt(total_value))
                best_arb = cross["best_sell"].max() - cross["best_buy"].min()
                c4.metric("最大跨区价差", fmt(best_arb))

                # 表格
                tbl = cross[["region", "best_buy", "best_sell", "bid_ask_spread",
                              "spread_ratio", "best_buy_vol", "best_sell_vol",
                              "station_score"]].copy()
                tbl["region"] = tbl["region"].apply(
                    lambda r: REGION_LABELS.get(r, r.capitalize())
                )
                tbl.columns = ["区域", "买价", "卖价", "站内价差",
                               "价差比%", "买深度", "卖深度", "评分"]
                for c in ["买价", "卖价", "站内价差"]:
                    tbl[c] = tbl[c].apply(
                        lambda x: f"{x:,.0f}" if pd.notna(x) else "-"
                    )
                tbl["价差比%"] = tbl["价差比%"].apply(
                    lambda x: f"{x:.1f}%" if pd.notna(x) else "-"
                )

                st.dataframe(tbl, use_container_width=True, hide_index=True)

                # 柱状图：各区域买/卖价对比
                chart_df = cross[["region", "best_buy", "best_sell"]].melt(
                    id_vars=["region"],
                    value_vars=["best_buy", "best_sell"],
                    var_name="type", value_name="price_isk",
                )
                chart_df["region"] = chart_df["region"].apply(
                    lambda r: REGION_LABELS.get(r, r.capitalize())
                )
                chart_df["type"] = chart_df["type"].replace(
                    {"best_buy": "买价(最高)", "best_sell": "卖价(最低)"}
                )

                fig = px.bar(
                    chart_df,
                    x="region",
                    y="price_isk",
                    color="type",
                    barmode="group",
                    title=f"{item_name} — 各区域价格对比",
                    labels={"region": "区域", "price_isk": "价格(ISK)", "type": ""},
                    color_discrete_map={
                        "买价(最高)": "#4CAF50", "卖价(最低)": "#FF5722",
                    },
                )
                fig.update_layout(
                    legend=dict(orientation="h", y=1.12),
                    margin=dict(t=50),
                    height=350,
                )
                fig.update_yaxes(tickformat=",.0f")
                st.plotly_chart(fig, use_container_width=True)

                # 套利标注：最低买入区 → 最高卖出区
                buy_min_row = cross.loc[cross["best_buy"].idxmin()]
                sell_max_row = cross.loc[cross["best_sell"].idxmax()]
                if buy_min_row["region"] != sell_max_row["region"]:
                    arb_profit = (
                        sell_max_row["best_sell"] - buy_min_row["best_buy"]
                    )
                    arb_profit_after = arb_profit - sell_max_row["best_sell"] * 0.033
                    st.success(
                        f"💡 最优套利路径：**{REGION_LABELS.get(buy_min_row['region'], buy_min_row['region'].capitalize())}** "
                        f"买 ({fmt(buy_min_row['best_buy'])}) → "
                        f"**{REGION_LABELS.get(sell_max_row['region'], sell_max_row['region'].capitalize())}** "
                        f"卖 ({fmt(sell_max_row['best_sell'])})  "
                        f"≈ 税后利润 **{fmt(arb_profit_after)} ISK**"
                    )


# ============================
# 模式 2：交易引擎
# ============================
elif mode == "交易引擎":
    st.subheader("🧠 交易引擎 · 多模型量化评估")
    st.caption("策略引擎生成提案 → 4 模型独立评审 → 共识决策")

    station_df = load_station_data("jita")
    arb_df = load_arb_data()

    col1, col2, col3 = st.columns(3)
    with col1:
        strategy_type = st.selectbox(
            "策略类型",
            ["站内挂单 (正常市场)", "即时套利 (漏洞)", "全部提案"],
        )
    with col2:
        max_proposals = st.slider("提案数量", 1, 10, 5)
    with col3:
        use_real_api = st.checkbox(
            "使用真实 DeepSeek API",
            value=False,
            help="需要设置 DEEPSEEK_API_KEY 环境变量",
        )

    if st.button("🚀 运行交易引擎", type="primary"):
        with st.spinner("正在运行策略引擎 + 多模型评审..."):
            if strategy_type == "即时套利 (漏洞)" and arb_df is not None:
                df = arb_df
            elif station_df is not None:
                df = station_df
            else:
                st.error("没有可用的市场数据，请先采集!")
                st.stop()

            config = StrategyConfig(min_score=50, min_profit=5000, min_depth=5)
            engine = StrategyEngine(config)

            if strategy_type == "即时套利 (漏洞)":
                proposals = engine.generate_instant_arb_proposals(df, max_proposals)
            else:
                proposals = engine.generate_station_trading_proposals(df, max_proposals)

            if not proposals:
                st.warning("没有符合条件的交易提案，请调整筛选条件")
                st.stop()

            st.success(f"策略引擎生成了 {len(proposals)} 个提案")

            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not use_real_api or not api_key:
                api_key = ""

            committee = create_standard_committee(api_key)
            consensus_engine = ConsensusEngine()

            for i, prop in enumerate(proposals):
                st.markdown("---")
                st.markdown(f"### 📋 提案 #{i + 1}")

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("物品", prop.item_name)
                col2.metric("动作", prop.action.upper())
                col3.metric("挂单价", f"{prop.price:,.0f} ISK")
                col4.metric("数量", f"{prop.volume} 件")

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("总成本", f"{prop.total_cost:,.0f} ISK")
                col2.metric("预期利润", f"{prop.estimated_profit:,.0f} ISK")
                col3.metric("RoC", f"{prop.estimated_roc:.1f}%")
                col4.metric("策略", prop.strategy_name)

                st.caption(f"**策略理由:** {prop.strategy_rationale}")

                with st.spinner(f"委员会评审中 ({len(committee)} 模型)..."):
                    evaluations = run_committee(prop, api_key=api_key, evaluators=committee)
                    consensus = consensus_engine.aggregate(prop, evaluations)

                verdict_emoji = {"execute": "✅ 执行", "review": "⚠️ 复核", "skip": "❌ 跳过"}
                st.markdown(
                    f"### 📊 共识结果: **{verdict_emoji.get(consensus.net_verdict, '❓')}**"
                )
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("均分", f"{consensus.avg_score:.1f}")
                col2.metric("分歧度", f"±{consensus.std_dev:.1f}")
                col3.metric("最低", f"{consensus.min_score:.0f}")
                col4.metric("最高", f"{consensus.max_score:.0f}")

                st.markdown("#### 各模型评审详情")
                ev_cols = st.columns(len(evaluations))
                for idx, ev in enumerate(evaluations):
                    with ev_cols[idx]:
                        st.markdown(f"**{ev.model_name}**")
                        score_color = "🟢" if ev.score >= 70 else "🟡" if ev.score >= 40 else "🔴"
                        verdict_icon = {"approve": "✅", "reject": "❌", "hold": "⏸️"}
                        st.markdown(f"评分: **{ev.score:.0f}** {score_color}")
                        st.markdown(f"判定: {verdict_icon.get(ev.verdict, '❓')} `{ev.verdict}`")
                        st.markdown(f"置信度: {ev.confidence:.0%}")
                        with st.expander("理由"):
                            st.write(ev.reasoning)
                        if ev.risk_flags:
                            st.warning(f"风险: {', '.join(ev.risk_flags)}")
                        if ev.suggestions:
                            st.info(f"建议: {', '.join(ev.suggestions)}")

                if consensus.disagreements:
                    st.warning(f"**主要分歧:** {', '.join(consensus.disagreements[:3])}")
                if consensus.suggestions_merged:
                    st.info(f"**综合建议:** {', '.join(consensus.suggestions_merged)}")

    else:
        st.info("配置好策略参数后，点击「🚀 运行交易引擎」开始评审")
        st.markdown("""
        ```
        📊 市场数据 ──→ 🧠 策略引擎 ──→ 🤖 委员会评审 ──→ ⚖️ 共识决策
                          │                 │                    │
                          ├ 站内挂单        ├ 基本面分析师      ├ ✅ 执行
                          ├ 即时套利        ├ 趋势交易员       ├ ⚠️ 复核
                          └ 区域搬运        ├ 风控官           └ ❌ 跳过
                                            └ 本地智能
        ```
        """)
