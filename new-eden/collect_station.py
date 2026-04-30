"""
EVE 多站挂单数据采集器

用法:
  python collect_station.py                   # Jita (默认)
  python collect_station.py --region=jita
  python collect_station.py --region=amarr

--region 可选: jita, amarr, dodixie, rens, hek
"""

import json
import time
import argparse
import concurrent.futures
from pathlib import Path
from datetime import datetime

import requests
import pandas as pd

ESI_BASE = "https://esi.evetech.net/latest"
USER_AGENT = "EveMarketMonitor/1.0 (station)"

# 五大贸易枢纽
REGIONS = {
    "jita":    {"id": 10000002, "name": "Jita (The Forge)"},
    "amarr":   {"id": 10000043, "name": "Amarr (Domain)"},
    "dodixie": {"id": 10000032, "name": "Dodixie (Sinq Laison)"},
    "rens":    {"id": 10000030, "name": "Rens (Heimatar)"},
    "hek":     {"id": 10000042, "name": "Hek (Metropolis)"},
}

DATA_DIR = Path(__file__).parent / "data"


def fetch_page(region_id: int, order_type: str, page: int) -> list | None:
    for attempt in range(3):
        try:
            resp = requests.get(
                f"{ESI_BASE}/markets/{region_id}/orders/",
                params={"order_type": order_type, "page": page},
                headers={"User-Agent": USER_AGENT},
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.json()
            print(f"  [WARN] {order_type} p{page} HTTP {resp.status_code}")
        except Exception as e:
            print(f"  [WARN] {order_type} p{page}: {e}")
        time.sleep(1)
    return None


def get_total_pages(region_id: int) -> tuple[int, int]:
    buy_r = requests.get(
        f"{ESI_BASE}/markets/{region_id}/orders/",
        params={"order_type": "buy", "page": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    sell_r = requests.get(
        f"{ESI_BASE}/markets/{region_id}/orders/",
        params={"order_type": "sell", "page": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    return int(buy_r.headers["X-Pages"]), int(sell_r.headers["X-Pages"])


def resolve_names(type_ids: set) -> dict[int, str]:
    ids_list = list(type_ids)
    names = {}
    for i in range(0, len(ids_list), 1000):
        batch = ids_list[i:i+1000]
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{ESI_BASE}/universe/names/",
                    json=batch,
                    headers={"User-Agent": USER_AGENT},
                    timeout=30,
                )
                if resp.status_code == 200:
                    for item in resp.json():
                        names[item["id"]] = item["name"]
                    break
            except Exception:
                time.sleep(1)
        if (i // 1000 + 1) % 5 == 0:
            print(f"  名称解析: {len(names)}/{len(ids_list)}")
        time.sleep(0.25)
    return names


def run(region_id: int = 10000002, output_name: str = "station_jita_full.csv"):
    t0 = datetime.now()
    region_label = REGIONS.get({v["id"]: k for k, v in REGIONS.items()}.get(region_id), {}).get("name", f"Region {region_id}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[{t0:%H:%M:%S}] {'='*50}")
    print(f"[{t0:%H:%M:%S}]  区域: {region_label} (ID={region_id})")
    print(f"[{t0:%H:%M:%S}] {'='*50}")

    print(f"[{t0:%H:%M:%S}] 获取总页数...")
    buy_pages, sell_pages = get_total_pages(region_id)
    total_pages = buy_pages + sell_pages
    print(f"  买单: {buy_pages} 页 | 卖单: {sell_pages} 页 | 合计: {total_pages}")

    print(f"[{datetime.now():%H:%M:%S}] 并发采集订单...")
    all_buy = []
    all_sell = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        buy_futures = {executor.submit(fetch_page, region_id, "buy", p): p for p in range(1, buy_pages + 1)}
        sell_futures = {executor.submit(fetch_page, region_id, "sell", p): p for p in range(1, sell_pages + 1)}

        done = 0
        for future in concurrent.futures.as_completed(buy_futures):
            r = future.result()
            if r: all_buy.extend(r)
            done += 1
            if done % 30 == 0:
                print(f"  buy: {done}/{buy_pages} ({(datetime.now()-t0).total_seconds():.0f}s)")

        done = 0
        for future in concurrent.futures.as_completed(sell_futures):
            r = future.result()
            if r: all_sell.extend(r)
            done += 1
            if done % 30 == 0:
                print(f"  sell: {done}/{sell_pages} ({(datetime.now()-t0).total_seconds():.0f}s)")

    print(f"[{datetime.now():%H:%M:%S}] 采集完成: {len(all_buy):,} 买, {len(all_sell):,} 卖")
    print(f"[{datetime.now():%H:%M:%S}] 归组订单...")

    buy_by_type = {}
    sell_by_type = {}
    for o in all_buy:
        buy_by_type.setdefault(o["type_id"], []).append(o)
    for o in all_sell:
        sell_by_type.setdefault(o["type_id"], []).append(o)

    all_types = set(buy_by_type.keys()) | set(sell_by_type.keys())
    print(f"[{datetime.now():%H:%M:%S}] 解析 {len(all_types)} 个名称...")
    names = resolve_names(all_types)
    print(f"  名称解析完成: {len(names)}/{len(all_types)}")

    print(f"[{datetime.now():%H:%M:%S}] 计算所有物品的指标...")
    common = set(buy_by_type.keys()) & set(sell_by_type.keys())
    rows = []

    for tid in common:
        buys = buy_by_type[tid]
        sells = sell_by_type[tid]

        best_buy = max(o["price"] for o in buys)
        best_buy_vol = sum(o["volume_remain"] for o in buys if o["price"] == best_buy)
        best_sell = min(o["price"] for o in sells)
        best_sell_vol = sum(o["volume_remain"] for o in sells if o["price"] == best_sell)

        total_buy_vol = sum(o["volume_remain"] for o in buys)
        total_sell_vol = sum(o["volume_remain"] for o in sells)

        buy_count = len(buys)
        sell_count = len(sells)

        # 价差（best_sell - best_buy，正常市场为正数）
        bid_ask_spread = best_sell - best_buy
        # 价差比
        if best_sell > 0:
            spread_ratio = (best_sell - best_buy) / best_sell * 100
        else:
            spread_ratio = 0

        # 毛利率（站内挂单：我们在价差内挂单，最多能吃的价差比例）
        # 假设买入出价比 best_buy 高 x%，卖出价比 best_sell 低 x%
        gross_margin = (best_sell - best_buy - best_sell * 0.033)  # 扣除3.3%税
        roc = (gross_margin / best_sell * 100) if best_sell > 0 else 0

        # 站内挂单评分：综合价差、深度、流动性
        # 价差分 (40%)
        if bid_ask_spread <= 0:
            spread_score = 0  # 即时套利，数据异常
        elif spread_ratio >= 20:
            spread_score = 40
        elif spread_ratio >= 10:
            spread_score = 35
        elif spread_ratio >= 5:
            spread_score = 28
        elif spread_ratio >= 3:
            spread_score = 20
        elif spread_ratio >= 1:
            spread_score = 12
        else:
            spread_score = 4

        # 深度分 (30%)
        depth = min(best_buy_vol, best_sell_vol)
        if depth >= 100:
            depth_score = 30
        elif depth >= 50:
            depth_score = 24
        elif depth >= 20:
            depth_score = 18
        elif depth >= 10:
            depth_score = 12
        elif depth >= 5:
            depth_score = 8
        elif depth >= 1:
            depth_score = 4
        else:
            depth_score = 0

        # 流动性分 (20%) — 挂单种数 + 总深度
        total_vol = total_buy_vol + total_sell_vol
        if total_vol >= 100000:
            liq_score = 20
        elif total_vol >= 10000:
            liq_score = 16
        elif total_vol >= 1000:
            liq_score = 12
        elif total_vol >= 100:
            liq_score = 8
        elif total_vol >= 10:
            liq_score = 4
        else:
            liq_score = 0

        # 订单密度分 (10%) — 挂单数量多说明竞争活跃
        total_orders = buy_count + sell_count
        if total_orders >= 100:
            ord_score = 10
        elif total_orders >= 50:
            ord_score = 8
        elif total_orders >= 20:
            ord_score = 6
        elif total_orders >= 10:
            ord_score = 4
        elif total_orders >= 5:
            ord_score = 2
        else:
            ord_score = 0

        station_score = spread_score + depth_score + liq_score + ord_score
        station_score = max(0, min(100, station_score))

        rows.append({
            "type_id": tid,
            "name": names.get(tid, f"unknown_{tid}"),
            "best_buy": best_buy,
            "best_sell": best_sell,
            "bid_ask_spread": round(bid_ask_spread, 2),
            "spread_ratio": round(spread_ratio, 2),
            "gross_margin": round(gross_margin, 2),
            "roc": round(roc, 2),
            "best_buy_vol": best_buy_vol,
            "best_sell_vol": best_sell_vol,
            "total_buy_vol": total_buy_vol,
            "total_sell_vol": total_sell_vol,
            "buy_order_count": buy_count,
            "sell_order_count": sell_count,
            "station_score": station_score,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("station_score", ascending=False)

    path = DATA_DIR / output_name
    df.to_csv(path, index=False)
    elapsed = (datetime.now() - t0).total_seconds()

    # 统计
    normal = df[df["bid_ask_spread"] > 0]
    arbitrage = df[df["bid_ask_spread"] <= 0]

    print(f"\n{'='*60}")
    print(f"站内挂单数据采集完成!")
    print(f"  总物品数: {len(df):,}")
    print(f"  正常市场 (适合挂单): {len(normal):,}")
    print(f"  即时套利 (spread<=0): {len(arbitrage):,}")
    print(f"  挂单评分 >= 60 的优质标的: {len(df[df['station_score'] >= 60]):,}")
    print(f"  保存至: {path}")
    print(f"  耗时: {elapsed:.0f} 秒")
    print(f"{'='*60}")

    print(f"\n🏆 站内挂单 Top 15:")
    print(f"{'物品':30s} {'卖价':>10s} {'买价':>10s} {'价差':>10s} {'价差比':>7s} {'利润率':>7s} {'深度':>5s} {'评分':>5s}")
    print(f"{'-'*85}")
    for _, r in df.head(15).iterrows():
        d = min(r["best_buy_vol"], r["best_sell_vol"])
        print(f"{str(r['name'])[:28]:28s} {r['best_sell']:>10,.0f} {r['best_buy']:>10,.0f} "
              f"{r['bid_ask_spread']:>10,.0f} {r['spread_ratio']:>6.1f}% {r['roc']:>6.1f}% "
              f"{d:>5,} {r['station_score']:>5.0f}")

    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EVE 多站挂单数据采集")
    parser.add_argument("--region", choices=list(REGIONS.keys()), default="jita",
                        help="交易枢纽 (默认: jita)")
    args = parser.parse_args()

    reg = REGIONS[args.region]
    output_name = f"station_{args.region}_full.csv"
    run(region_id=reg["id"], output_name=output_name)
