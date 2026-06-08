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

注意：从中国 ECS 访问需要 proxychains 绕过 Cloudflare 防护。
"""

import json
import subprocess
import time
from datetime import datetime

import pandas as pd

from config.settings import (
    OSRS_GE_API, OSRS_MAPPING_API, OSRS_5M_API,
    OSRS_USER_AGENT, DATA_DIR, WATCHED_ITEMS
)


def _curl(url: str) -> dict:
    """通过 proxychains + curl 获取 JSON（绕过 Cloudflare）"""
    result = subprocess.run(
        ["proxychains4", "-q", "curl", "-s", "--max-time", "30",
         "-H", f"User-Agent: {OSRS_USER_AGENT}",
         url],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed (exit {result.returncode}): {result.stderr}")
    if not result.stdout.strip():
        raise RuntimeError(f"curl returned empty response from {url}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON parse error for {url}: {e}\nResponse: {result.stdout[:300]}")


def fetch_mapping() -> dict:
    """获取物品 ID → 名称、限购量、会员标志"""
    items = _curl(OSRS_MAPPING_API)
    result = {}
    for item in items:
        result[item["id"]] = {
            "name": item["name"],
            "buy_limit": item.get("limit", 0),      # GE 每 4h 限购
            "is_member": item.get("members", True),  # 会员专属
        }
    return result


def fetch_latest_prices() -> dict:
    """获取所有物品的最新价格"""
    return _curl(OSRS_GE_API)["data"]


def fetch_volume_data() -> dict:
    """获取 5 分钟交易量"""
    return _curl(OSRS_5M_API)["data"]


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
