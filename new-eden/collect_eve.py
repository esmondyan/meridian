"""
EVE Online Jita 4-4 市场订单采集器（轻量原型版）

使用 CCP ESI API（免费、无需 Key）
Jita 所在区域：The Forge (region_id=10000002)

策略：只爬前 N 页，速度够快出原型
"""

import json
import time
from pathlib import Path
from datetime import datetime

import requests
import pandas as pd

ESI_BASE = "https://esi.evetech.net/latest"
REGION_ID = 10000002  # The Forge
USER_AGENT = "EveMarketMonitor/1.0 (prototype)"
MAX_PAGES = 5  # 原型阶段只爬 5 页（~5000 条/边）

DATA_DIR = Path(__file__).parent / "data"


def fetch_orders(order_type: str, page: int = 1) -> list[dict] | None:
    """取一页订单。order_type: 'buy' 或 'sell'"""
    try:
        resp = requests.get(
            f"{ESI_BASE}/markets/{REGION_ID}/orders/",
            params={"order_type": order_type, "page": page},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  [WARN] {order_type} page {page} 失败: {e}")
        return None


def get_page_count() -> int:
    """快速获取总页数"""
    resp = requests.get(
        f"{ESI_BASE}/markets/{REGION_ID}/orders/",
        params={"order_type": "buy", "page": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    return int(resp.headers.get("X-Pages", 0))


def collect_orders() -> tuple[dict[int, list], dict[int, list]]:
    """获取买单和卖单（限 MAX_PAGES 页），按 type_id 归组"""
    total_pages = get_page_count()
    pages_to_fetch = min(MAX_PAGES, total_pages)
    print(f"[{datetime.now():%H:%M:%S}] 采集 Jita 市场（{pages_to_fetch}/{total_pages} 页）...")

    buy_orders: dict[int, list] = {}
    sell_orders: dict[int, list] = {}

    for order_type, store in [("buy", buy_orders), ("sell", sell_orders)]:
        count = 0
        for page in range(1, pages_to_fetch + 1):
            orders = fetch_orders(order_type, page)
            if not orders:
                break
            for o in orders:
                tid = o["type_id"]
                store.setdefault(tid, []).append(o)
            count += len(orders)
            print(f"  {order_type} page {page}/{pages_to_fetch}: {len(orders)} 条")
            if page < pages_to_fetch:
                time.sleep(0.3)
        print(f"  {order_type} 合计: {count} 条, {len(store)} 种物品\n")

    return buy_orders, sell_orders


def resolve_names(type_ids: set[int]) -> dict[int, str]:
    """批量解析 type_id -> 物品名称（一次最多 1000 个）"""
    ids_list = list(type_ids)
    names: dict[int, str] = {}

    for i in range(0, len(ids_list), 1000):
        batch = ids_list[i : i + 1000]
        try:
            resp = requests.post(
                f"{ESI_BASE}/universe/names/",
                json=batch,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            resp.raise_for_status()
            for item in resp.json():
                names[item["id"]] = item["name"]
            print(f"  已解析 {len(names)}/{len(ids_list)} 个")
        except Exception as e:
            print(f"  [WARN] name 解析失败: {e}")
        time.sleep(0.2)

    return names


def calc_eve_score(row: dict) -> float:
    """EVE 倒卖评分（0-100），适配 EVE 的特性"""
    spread = row["spread"]
    best_sell = row["best_sell"]
    spread_ratio = row["spread_ratio"]
    best_buy_vol = row["best_buy_vol"]
    best_sell_vol = row["best_sell_vol"]
    profit = row["profit_after_tax"]

    if spread <= 0 or best_sell <= 0:
        return 0.0

    # --- 维度 1: 价差比得分 (30%) ---
    # EVE 价差通常比 OSRS 大，但3.3%税会吃掉薄利
    if spread_ratio >= 20:
        sr_score = 30
    elif spread_ratio >= 10:
        sr_score = 25
    elif spread_ratio >= 5:
        sr_score = 18
    elif spread_ratio >= 3:
        sr_score = 12
    elif spread_ratio >= 1:
        sr_score = 6
    else:
        sr_score = 2

    # --- 维度 2: 绝对利润得分 (25%) ---
    # 按 ISK 量级打分
    if profit >= 10_000_000:
        pf_score = 25
    elif profit >= 1_000_000:
        pf_score = 20
    elif profit >= 100_000:
        pf_score = 14
    elif profit >= 10_000:
        pf_score = 8
    elif profit >= 1_000:
        pf_score = 4
    else:
        pf_score = 1

    # --- 维度 3: 最优价位深度得分 (20%) ---
    # 能在最优买卖价成交多少单
    depth = min(best_buy_vol, best_sell_vol)
    if depth >= 100:
        dp_score = 20
    elif depth >= 50:
        dp_score = 16
    elif depth >= 20:
        dp_score = 12
    elif depth >= 10:
        dp_score = 8
    elif depth >= 5:
        dp_score = 5
    elif depth >= 1:
        dp_score = 2
    else:
        dp_score = 0

    # --- 维度 4: 资金效率/RoC 得分 (15%) ---
    roc = profit / best_sell * 100
    if roc >= 20:
        roc_score = 15
    elif roc >= 10:
        roc_score = 12
    elif roc >= 5:
        roc_score = 8
    elif roc >= 2:
        roc_score = 5
    elif roc >= 1:
        roc_score = 3
    else:
        roc_score = 1

    # --- 维度 5: 订单深度杠杆 (10%) ---
    # 挂单数量/总量占比高说明市场活跃
    total_vol = row.get("total_buy_vol", 0) + row.get("total_sell_vol", 0)
    if total_vol >= 10_000:
        tv_score = 10
    elif total_vol >= 1_000:
        tv_score = 7
    elif total_vol >= 100:
        tv_score = 4
    elif total_vol >= 10:
        tv_score = 2
    else:
        tv_score = 0

    # --- 惩罚: 深度为 0 ---
    penalty = 0
    if best_buy_vol == 0 or best_sell_vol == 0:
        penalty = 20  # 最优价位没成交量，挂单可能很久卖不出去
    elif depth < 5:
        penalty = 10  # 深度太浅

    total = sr_score + pf_score + dp_score + roc_score + tv_score - penalty
    return max(0, min(100, total))


def build_flip_df(buy_orders: dict, sell_orders: dict) -> pd.DataFrame:
    """构建 EVE 倒卖分析 DataFrame"""
    rows = []

    common_types = set(buy_orders.keys()) & set(sell_orders.keys())
    print(f"  既有买单又有卖单的物品: {len(common_types)} 个")

    for tid in common_types:
        buys = buy_orders[tid]
        sells = sell_orders[tid]

        best_buy = max(o["price"] for o in buys)
        best_buy_vol = sum(
            o["volume_remain"] for o in buys if o["price"] == best_buy
        )
        best_sell = min(o["price"] for o in sells)
        best_sell_vol = sum(
            o["volume_remain"] for o in sells if o["price"] == best_sell
        )

        spread = best_buy - best_sell
        spread_ratio = (spread / best_sell * 100) if best_sell > 0 else 0
        profit_after_tax = spread - best_sell * 0.033

        # 总深度
        total_buy_vol = sum(o["volume_remain"] for o in buys)
        total_sell_vol = sum(o["volume_remain"] for o in sells)

        row = {
            "type_id": tid,
            "best_buy": best_buy,
            "best_sell": best_sell,
            "spread": round(spread, 2),
            "spread_ratio": round(spread_ratio, 2),
            "profit_after_tax": round(profit_after_tax, 2),
            "best_buy_vol": best_buy_vol,
            "best_sell_vol": best_sell_vol,
            "total_buy_vol": total_buy_vol,
            "total_sell_vol": total_sell_vol,
            "buy_order_count": len(buys),
            "sell_order_count": len(sells),
        }

        # 计算评分
        row["eve_score"] = round(calc_eve_score(row), 1)
        row["capital_needed"] = round(best_sell * min(best_sell_vol, 10), 0)
        row["roc"] = round(
            (profit_after_tax / best_sell * 100) if best_sell > 0 else 0, 2
        )

        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        # 过滤掉负利润
        df = df[df["profit_after_tax"] > 0]
        df = df.sort_values("eve_score", ascending=False)
    return df


def run_once() -> pd.DataFrame:
    """跑一轮完整采集"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    buy_orders, sell_orders = collect_orders()

    all_type_ids = set(buy_orders.keys()) | set(sell_orders.keys())
    print(f"解析物品名称 ({len(all_type_ids)} 个)...")
    names = resolve_names(all_type_ids)

    df = build_flip_df(buy_orders, sell_orders)
    df["name"] = df["type_id"].map(names)

    # 保存
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    latest_path = DATA_DIR / "jita_latest.csv"
    ts_path = DATA_DIR / f"jita_{ts}.csv"
    df.to_csv(latest_path, index=False)
    df.to_csv(ts_path, index=False)
    print(f"\n[{datetime.now():%H:%M:%S}] 已保存 {len(df)} 条倒卖机会")

    # 打印 Top 15
    top = df.head(15)
    print(f"\n{'='*100}")
    print(f"🏆 EVE Jita 倒卖评分 Top 15")
    print(f"{'='*100}")
    print(
        f"{'物品':30s} {'卖价':>12s} {'买价':>12s} {'利润':>12s} {'价差比':>8s} {'RoC':>7s} {'深度':>6s} {'评分':>5s}"
    )
    print(f"{'-'*100}")
    for _, r in top.iterrows():
        print(
            f"{str(r['name'])[:28]:28s} "
            f"{r['best_sell']:>12,.0f} "
            f"{r['best_buy']:>12,.0f} "
            f"{r['profit_after_tax']:>12,.0f} "
            f"{r['spread_ratio']:>7.1f}% "
            f"{r['roc']:>6.1f}% "
            f"{min(r['best_buy_vol'], r['best_sell_vol']):>5,} "
            f"{r['eve_score']:>5.1f}"
        )

    return df


if __name__ == "__main__":
    t0 = datetime.now()
    df = run_once()
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n⏱ 耗时 {elapsed:.1f} 秒")
