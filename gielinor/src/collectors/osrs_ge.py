"""
OSRS Grand Exchange 价格采集器

使用 OSRS Wiki 官方 API（免费、无需 Key）：
- 最新价格: https://prices.runescape.wiki/api/v1/osrs/latest
- 物品映射: https://prices.runescape.wiki/api/v1/osrs/mapping
- 5分钟交易量: https://prices.runescape.wiki/api/v1/osrs/5m

数据说明：
- high: 卖家挂单价（你想卖的价格）
- low:  买家出价（你想买的价格）
- buy_limit: GE 每 4 小时限购量
- volume_buy:  5 分钟内买盘成交量
- volume_sell: 5 分钟内卖盘成交量
- is_member: 是否为会员专属物品
"""

import json
import time
from datetime import datetime

import requests
import pandas as pd

from config.settings import (
    OSRS_GE_API, OSRS_MAPPING_API, OSRS_5M_API,
    OSRS_USER_AGENT, DATA_DIR, WATCHED_ITEMS
)


def fetch_mapping() -> dict:
    """获取物品 ID → 名称、限购量、会员标志"""
    resp = requests.get(
        OSRS_MAPPING_API,
        headers={"User-Agent": OSRS_USER_AGENT}
    )
    resp.raise_for_status()
    items = resp.json()
    result = {}
    for item in items:
        result[item["id"]] = {
            "name": item["name"],
            "buy_limit": item.get("limit", 0),     # GE 每 4h 限购
            "is_member": item.get("members", True),  # 会员专属
        }
    return result


def fetch_latest_prices() -> dict:
    """获取所有物品的最新价格"""
    resp = requests.get(
        OSRS_GE_API,
        headers={"User-Agent": OSRS_USER_AGENT}
    )
    resp.raise_for_status()
    return resp.json()["data"]


def fetch_volume_data() -> dict:
    """获取 5 分钟交易量"""
    resp = requests.get(
        OSRS_5M_API,
        headers={"User-Agent": OSRS_USER_AGENT}
    )
    resp.raise_for_status()
    return resp.json()["data"]


def build_price_df() -> pd.DataFrame:
    """组装价格 DataFrame（含价格、交易量、限购量）"""
    mapping = fetch_mapping()
    prices = fetch_latest_prices()
    volumes = fetch_volume_data()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")  # 统一用空格分隔

    rows = []
    for item_id_str, price_data in prices.items():
        item_id = int(item_id_str)
        meta = mapping.get(item_id, {})
        name = meta.get("name", f"unknown_{item_id}")

        if WATCHED_ITEMS and item_id not in WATCHED_ITEMS:
            continue

        high = price_data.get("high", 0) or 0
        low = price_data.get("low", 0) or 0
        spread = max(0, high - low)  # 防负价差

        # 交易量
        vol_data = volumes.get(item_id_str, {})
        volume_buy = vol_data.get("highPriceVolume", 0) or 0
        volume_sell = vol_data.get("lowPriceVolume", 0) or 0

        rows.append({
            "item_id": item_id,
            "name": name,
            "high": high,
            "low": low,
            "spread": spread,
            "buy_limit": meta.get("buy_limit", 0),
            "is_member": meta.get("is_member", True),
            "volume_buy": volume_buy,
            "volume_sell": volume_sell,
            "volume_total": volume_buy + volume_sell,
            "timestamp": now,
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("spread", ascending=False)
    return df


def save_prices(df: pd.DataFrame):
    """保存到 SQLite"""
    # SQLite（更快查询）
    try:
        from src.storage.db import init_db, insert_snapshot
        init_db()
        insert_snapshot(df)
        print(f"[{datetime.now():%H:%M:%S}] 已同步 → SQLite")
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] SQLite 写入失败: {e}")


def run_once():
    """跑一轮采集"""
    print(f"[{datetime.now():%H:%M:%S}] 开始采集 OSRS 价格...")
    try:
        df = build_price_df()
        save_prices(df)

        # 打印 Top 10 交易量
        top_vol = df.nlargest(10, "volume_total")
        print("\n📊 交易量 Top 10：")
        for _, row in top_vol.iterrows():
            print(f"  {row['name']:25s}  买量 {row['volume_buy']:>8,}  卖量 {row['volume_sell']:>8,}  限购 {row['buy_limit']:>5,}")
        print()
    except Exception as e:
        print(f"❌ 采集失败: {e}")


if __name__ == "__main__":
    run_once()
