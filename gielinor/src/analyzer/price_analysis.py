"""
价格分析逻辑
"""

import pandas as pd


def load_osrs_prices() -> pd.DataFrame:
    """
    加载价格数据，仅从 SQLite 读取
    """
    try:
        from src.storage.db import get_all_prices
        df = get_all_prices(since_days=14)  # 只取最近 14 天，量小速度快
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
            return df
    except Exception as e:
        print(f"  [load_osrs_prices] SQLite 加载失败: {e}")

    return pd.DataFrame()


def get_latest_prices(df: pd.DataFrame = None) -> pd.DataFrame:
    """取每个物品最新一条记录，含价差和价差比。
    优先从 SQLite 直接查（快），回退到 DataFrame 计算。
    """
    # 从 SQLite 直接查（用 ROW_NUMBER 窗口函数，避免全表加载）
    try:
        from src.storage.db import get_latest_prices_df
        latest = get_latest_prices_df()
        if not latest.empty:
            latest["spread"] = latest["high"].fillna(0) - latest["low"].fillna(0)
            latest["spread_ratio"] = latest["spread"] / latest["low"].replace(0, 1) * 100
            latest["spread_ratio"] = latest["spread_ratio"].clip(lower=0)
            return latest
    except Exception:
        pass

    # 回退到 DataFrame 计算
    if df is None or df.empty:
        df = load_osrs_prices()
    latest = df.sort_values("timestamp").groupby("item_id").last().reset_index()
    latest["spread"] = latest["high"] - latest["low"]
    latest["spread_ratio"] = latest["spread"] / latest["low"] * 100
    latest["spread_ratio"] = latest["spread_ratio"].clip(lower=0)
    return latest


def get_price_trend(df: pd.DataFrame, item_name: str, window: int = 10) -> pd.DataFrame:
    """取某个物品的价格趋势（优先从 SQLite 直接查）"""
    # 尝试从 SQLite 直接查（避免全表扫描）
    try:
        from src.storage.db import get_price_trend_df
        item_df = get_price_trend_df(item_name)
        if not item_df.empty:
            item_df = item_df.sort_values("timestamp")
            item_df["timestamp"] = pd.to_datetime(item_df["timestamp"], format="mixed")
            item_df["high_ma"] = item_df["high"].rolling(window).mean()
            item_df["low_ma"] = item_df["low"].rolling(window).mean()
            return item_df
    except Exception:
        pass

    # 回退到 DataFrame 过滤
    item_df = df[df["name"] == item_name].copy()
    if item_df.empty:
        return pd.DataFrame()
    item_df = item_df.sort_values("timestamp")
    item_df["high_ma"] = item_df["high"].rolling(window).mean()
    item_df["low_ma"] = item_df["low"].rolling(window).mean()
    return item_df


def calc_spread_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """计算价差比例 (high - low) / low * 100"""
    latest = get_latest_prices(df)
    latest["spread_ratio"] = (latest["high"] - latest["low"]) / latest["low"] * 100
    latest["spread_ratio"] = latest["spread_ratio"].clip(lower=0)
    return latest.sort_values("spread_ratio", ascending=False)


from src.analyzer.scoring_config import load_scoring_config, replace_inf


def calc_flip_probability(df: pd.DataFrame = None) -> pd.DataFrame:
    """
    计算每个物品的倒卖概率评分 (0~100)。

    评分维度、权重、分桶全部从 config/scoring_*.json 读取。
    修改 JSON 即调参，无需改代码。
    """
    from src.storage.db import get_latest_prices_df as db_latest
    try:
        latest = db_latest()
    except Exception:
        latest = get_latest_prices(df)

    # 加载评分配置
    cfg = load_scoring_config()
    w = cfg["weights"]
    bins = cfg["bins"]
    th = cfg["thresholds"]

    # 用低值填充空值
    for col in ["volume_total", "buy_limit"]:
        if col not in latest.columns:
            latest[col] = 0

    # 确保有 spread_ratio
    if "spread_ratio" not in latest.columns:
        latest["spread_ratio"] = (
            (latest["high"].fillna(0) - latest["low"].fillna(0))
            / latest["low"].replace(0, 1) * 100
        ).clip(lower=0)

    # 确保有 spread
    if "spread" not in latest.columns:
        latest["spread"] = latest["high"].fillna(0) - latest["low"].fillna(0)

    # ===== 数据准备 =====
    high = latest["high"].fillna(0)
    spread = latest["spread"].fillna(0)
    vol = latest["volume_total"].fillna(0)
    lim = latest["buy_limit"].fillna(0)
    sr = latest["spread_ratio"].fillna(0)

    # 税后利润（保留负值用于过滤，不 clip）
    raw_profit = spread - high * 0.02
    latest["profit_after_tax"] = raw_profit.clip(lower=0)

    # RoC = 税后利润 / 投入资金 * 100
    roc = (raw_profit / high.replace(0, 1) * 100).clip(lower=0, upper=50)
    latest["roc"] = roc.round(2)

    # ===== 1. 交易量得分 =====
    vol_score = pd.cut(
        vol,
        bins=replace_inf(bins["vol"]),
        labels=bins["vol_labels"],
        ordered=False,
    ).astype(float)
    latest["vol_score"] = vol_score
    vol_penalty = (vol == 0).astype(float) * w["vol_penalty"]

    # ===== 2. 利润得分 =====
    sweet = bins["profit_sweet_spot"]
    max_ps = w["profit_score_max"]
    profit_score = (
        (sr >= sweet[0]) & (sr <= sweet[1])
    ) * max_ps + (
        (sr > sweet[1]) & (sr <= 15)
    ) * (max_ps - (sr - sweet[1]) / 7 * 10) + (
        (sr > 15) & (sr <= 30)
    ) * 15 + (
        sr > 30
    ) * 8 + (
        (sr > 0) & (sr < sweet[0])
    ) * (sr / sweet[0] * max_ps * 0.6)
    latest["profit_score"] = profit_score.clip(0, max_ps)

    # ===== 3. 限购得分 =====
    limit_score = pd.cut(
        lim,
        bins=replace_inf(bins["limit"]),
        labels=bins["limit_labels"],
        ordered=False,
    ).astype(float)
    latest["limit_score"] = limit_score

    # ===== 4. 税后利润得分 =====
    tax_score = pd.cut(
        latest["profit_after_tax"],
        bins=replace_inf(bins["tax"]),
        labels=bins["tax_labels"],
        ordered=False,
    ).astype(float)
    latest["tax_score"] = tax_score

    # ===== 5. RoC 得分 =====
    roc_score = pd.cut(
        roc,
        bins=replace_inf(bins["roc"]),
        labels=bins["roc_labels"],
        ordered=False,
    ).astype(float)
    latest["roc_score"] = roc_score

    # ===== 6. 成交量可信度（防庄家操控） =====
    # 检查买卖双方成交量是否均衡，判断价差是否可靠
    # 庄家手法：挂高价买单假装需求旺盛（buy_vol>0, sell_vol=0）
    #          或挂低价卖单假装供应充足（sell_vol>0, buy_vol=0）
    has_split = "volume_buy" in latest.columns and "volume_sell" in latest.columns
    if has_split:
        buy_v = latest["volume_buy"].fillna(0)
        sell_v = latest["volume_sell"].fillna(0)
        max_penalty = w["volume_balance_max_penalty"]

        # 双侧均无成交量 → 价盘纯假，重罚
        dead_market = (buy_v == 0) & (sell_v == 0)
        # 单侧无成交量 → 价盘不可靠，中罚
        one_sided = ((buy_v == 0) & (sell_v > 0)) | ((sell_v == 0) & (buy_v > 0))
        # 严重不均衡（>10倍）→ 轻罚
        max_side = pd.concat([buy_v, sell_v], axis=1).max(axis=1).replace(0, 1)
        min_side = pd.concat([buy_v, sell_v], axis=1).min(axis=1)
        ratio = max_side / min_side.replace(0, 1)
        imbalanced = (ratio > 10) & ~one_sided & ~dead_market

        vol_balance_penalty = (
            dead_market.astype(float) * max_penalty +
            one_sided.astype(float) * (max_penalty * 0.75) +
            imbalanced.astype(float) * (max_penalty * 0.4)
        ).clip(0, max_penalty)
        latest["vol_balance_penalty"] = vol_balance_penalty
    else:
        vol_balance_penalty = pd.Series(0.0, index=latest.index)
        latest["vol_balance_penalty"] = 0

    # ===== 综合评分 =====
    flip_score = (
        vol_score + profit_score + limit_score +
        tax_score + roc_score - vol_penalty - vol_balance_penalty
    ).clip(0, 100).round(1)

    # ===== 负利润封顶 =====
    negative_profit = raw_profit < 0
    cap = th["negative_profit_cap"]
    flip_score = flip_score.mask(negative_profit, flip_score.clip(upper=cap))
    dead = negative_profit & (vol == 0)
    flip_score = flip_score.mask(dead, 0.0)
    latest["flip_score"] = flip_score

    # ===== 极低利润惩罚 =====
    # 利润 < 50 GP 的物品，其他维度（成交量、价差比、限购、RoC）得分的信任度打折
    # 解决"火符文利润1GP也能得80分"的问题
    tiny_profit = latest["profit_after_tax"] < 50
    profit_ratio = (latest["profit_after_tax"] / 50).clip(lower=0, upper=1)
    # 非绝对利润维度（vol + profit + limit + roc）乘以折扣系数，tax_score保留
    non_tax_components = vol_score + profit_score + limit_score + roc_score - vol_penalty
    adjusted = non_tax_components * profit_ratio + tax_score
    flip_score = flip_score.mask(tiny_profit, adjusted)
    flip_score = flip_score.clip(0, 100).round(1)
    latest["flip_score"] = flip_score

    # ===== 日利润估算（合理版） =====
    # 使用更保守的日成交量估计：5min量 × 288时段 × 25%
    # 同时受限购约束：每4小时限购一次，一天最多6次
    daily_volume_est = vol * 288 * 0.25  # 保守日成交量（25%×全日时段）
    cycles_per_day = pd.concat([lim * 6, daily_volume_est], axis=1).min(axis=1).fillna(0)
    latest["daily_profit"] = (latest["profit_after_tax"] * cycles_per_day).round(0)

    # ===== 评级 =====
    def grade(s):
        if s >= th["grade_aplus"]: return "⭐ A+"
        elif s >= th["grade_a"]:   return "👍 A"
        elif s >= th["grade_b"]:   return "👌 B"
        elif s >= th["grade_c"]:   return "⚠️ C"
        else:                       return "❌ D"
    latest["flip_grade"] = flip_score.apply(grade)

    # A+ 最低利润门槛：利润太少（< 50 GP）不配 A+
    min_profit = th.get("min_profit_for_aplus", 50)
    too_small = latest["profit_after_tax"] < min_profit
    latest.loc[too_small & (latest["flip_grade"] == "⭐ A+"), "flip_grade"] = "👍 A"

    return latest.sort_values("flip_score", ascending=False)


def find_arbitrage_opportunities(
    g2g_price: float,
    osrs_high: float,
    fee_rate: float = 0.05
) -> dict | None:
    """
    简单套利计算：
    在 G2G 买金币 → 在游戏内高价卖掉 → 扣除平台费后是否有利润
    """
    cost = g2g_price * (1 + fee_rate)
    revenue = osrs_high * 0.98  # GE 交易税 2%

    profit = revenue - cost
    roi = profit / cost * 100 if cost > 0 else 0

    return {
        "g2g_price": g2g_price,
        "osrs_high": osrs_high,
        "cost": round(cost, 2),
        "revenue": round(revenue, 2),
        "profit": round(profit, 2),
        "roi": round(roi, 2),
        "profitable": profit > 0,
    }
