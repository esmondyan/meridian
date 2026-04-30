"""
EVE Jita 全量采集器（并发版）

使用 concurrent.futures 并发请求 ESI，提速全量采集。
"""

import json
import time
import concurrent.futures
from pathlib import Path
from datetime import datetime
from functools import lru_cache

import requests
import pandas as pd

ESI_BASE = "https://esi.evetech.net/latest"
REGION_ID = 10000002
USER_AGENT = "EveMarketMonitor/1.0 (fullscan)"
DATA_DIR = Path(__file__).parent / "data"


def fetch_page(order_type: str, page: int) -> list[dict] | None:
    """取一页订单，带重试"""
    for attempt in range(3):
        try:
            resp = requests.get(
                f"{ESI_BASE}/markets/{REGION_ID}/orders/",
                params={"order_type": order_type, "page": page},
                headers={"User-Agent": USER_AGENT},
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.json()
            print(f"  [WARN] {order_type} p{page} HTTP {resp.status_code}, retry {attempt+1}")
        except Exception as e:
            print(f"  [WARN] {order_type} p{page} fail: {e}, retry {attempt+1}")
        time.sleep(1)
    return None


def get_total_pages() -> tuple[int, int]:
    """获取买单和卖单的总页数"""
    buy_resp = requests.get(
        f"{ESI_BASE}/markets/{REGION_ID}/orders/",
        params={"order_type": "buy", "page": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    sell_resp = requests.get(
        f"{ESI_BASE}/markets/{REGION_ID}/orders/",
        params={"order_type": "sell", "page": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    return int(buy_resp.headers["X-Pages"]), int(sell_resp.headers["X-Pages"])


def resolve_names(type_ids: set[int], progress_cb=None) -> dict[int, str]:
    """批量解析 type_id -> 物品名称"""
    ids_list = list(type_ids)
    names: dict[int, str] = {}
    for i in range(0, len(ids_list), 1000):
        batch = ids_list[i : i + 1000]
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
        if progress_cb:
            progress_cb(len(names), len(ids_list))
        time.sleep(0.25)
    return names


def run_full_scan(output_name: str = "jita_full.csv"):
    """
    全量采集 Jita 所有订单。
    返回 (df, buy_count, sell_count)
    """
    t0 = datetime.now()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[{datetime.now():%H:%M:%S}] 获取总页数...")
    buy_pages, sell_pages = get_total_pages()
    total_pages = buy_pages + sell_pages
    print(f"  买单: {buy_pages} 页 | 卖单: {sell_pages} 页 | 合计: {total_pages} 页")

    # === 阶段1: 并发拉取所有订单 ===
    print(f"[{datetime.now():%H:%M:%S}] 开始并发采集...")

    all_buy = []
    all_sell = []

    # 构建所有任务
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        buy_futures = {
            executor.submit(fetch_page, "buy", p): p for p in range(1, buy_pages + 1)
        }
        sell_futures = {
            executor.submit(fetch_page, "sell", p): p for p in range(1, sell_pages + 1)
        }

        # 收集买单结果
        done = 0
        for future in concurrent.futures.as_completed(buy_futures):
            page = buy_futures[future]
            result = future.result()
            if result:
                all_buy.extend(result)
                done += 1
            else:
                print(f"  [FAIL] buy p{page}")
            if done % 20 == 0:
                elapsed = (datetime.now() - t0).total_seconds()
                print(f"  buy 进度: {done}/{buy_pages} ({elapsed:.0f}s)")

        # 收集卖单结果
        done = 0
        for future in concurrent.futures.as_completed(sell_futures):
            page = sell_futures[future]
            result = future.result()
            if result:
                all_sell.extend(result)
                done += 1
            else:
                print(f"  [FAIL] sell p{page}")
            if done % 20 == 0:
                elapsed = (datetime.now() - t0).total_seconds()
                print(f"  sell 进度: {done}/{sell_pages} ({elapsed:.0f}s)")

    print(f"[{datetime.now():%H:%M:%S}] 采集完成: {len(all_buy)} 买单, {len(all_sell)} 卖单")

    # === 阶段2: 按 type_id 归组 ===
    print(f"[{datetime.now():%H:%M:%S}] 归组订单...")
    buy_by_type: dict[int, list] = {}
    sell_by_type: dict[int, list] = {}
    for o in all_buy:
        buy_by_type.setdefault(o["type_id"], []).append(o)
    for o in all_sell:
        sell_by_type.setdefault(o["type_id"], []).append(o)

    # === 阶段3: 解析物品名称 ===
    all_type_ids = set(buy_by_type.keys()) | set(sell_by_type.keys())
    print(f"[{datetime.now():%H:%M:%S}] 解析 {len(all_type_ids)} 个物品名称...")

    def print_progress(done, total):
        if done % 2000 == 0:
            print(f"  名称解析: {done}/{total}")

    names = resolve_names(all_type_ids, print_progress)
    print(f"  名称解析完成: {len(names)}/{len(all_type_ids)}")

    # === 阶段4: 构建倒卖分析 ===
    print(f"[{datetime.now():%H:%M:%S}] 计算倒卖评分...")
    common = set(buy_by_type.keys()) & set(sell_by_type.keys())
    rows = []
    skipped = 0

    for tid in common:
        buys = buy_by_type[tid]
        sells = sell_by_type[tid]

        best_buy = max(o["price"] for o in buys)
        best_buy_vol = sum(o["volume_remain"] for o in buys if o["price"] == best_buy)
        best_sell = min(o["price"] for o in sells)
        best_sell_vol = sum(o["volume_remain"] for o in sells if o["price"] == best_sell)

        spread = best_buy - best_sell
        if spread <= 0:
            skipped += 1
            continue

        spread_ratio = spread / best_sell * 100
        profit_after_tax = spread - best_sell * 0.033

        total_buy_vol = sum(o["volume_remain"] for o in buys)
        total_sell_vol = sum(o["volume_remain"] for o in sells)

        # 评分
        sr_score = 0
        if spread_ratio >= 20: sr_score = 30
        elif spread_ratio >= 10: sr_score = 25
        elif spread_ratio >= 5: sr_score = 18
        elif spread_ratio >= 3: sr_score = 12
        elif spread_ratio >= 1: sr_score = 6
        else: sr_score = 2

        if profit_after_tax >= 10_000_000: pf_score = 25
        elif profit_after_tax >= 1_000_000: pf_score = 20
        elif profit_after_tax >= 100_000: pf_score = 14
        elif profit_after_tax >= 10_000: pf_score = 8
        elif profit_after_tax >= 1_000: pf_score = 4
        else: pf_score = 1

        depth = min(best_buy_vol, best_sell_vol)
        if depth >= 100: dp_score = 20
        elif depth >= 50: dp_score = 16
        elif depth >= 20: dp_score = 12
        elif depth >= 10: dp_score = 8
        elif depth >= 5: dp_score = 5
        elif depth >= 1: dp_score = 2
        else: dp_score = 0

        roc = profit_after_tax / best_sell * 100
        if roc >= 20: roc_score = 15
        elif roc >= 10: roc_score = 12
        elif roc >= 5: roc_score = 8
        elif roc >= 2: roc_score = 5
        elif roc >= 1: roc_score = 3
        else: roc_score = 1

        total_vol = total_buy_vol + total_sell_vol
        if total_vol >= 10_000: tv_score = 10
        elif total_vol >= 1_000: tv_score = 7
        elif total_vol >= 100: tv_score = 4
        elif total_vol >= 10: tv_score = 2
        else: tv_score = 0

        penalty = 0
        if best_buy_vol == 0 or best_sell_vol == 0: penalty = 20
        elif depth < 5: penalty = 10

        eve_score = max(0, min(100, sr_score + pf_score + dp_score + roc_score + tv_score - penalty))

        rows.append({
            "type_id": tid,
            "name": names.get(tid, f"unknown_{tid}"),
            "best_buy": best_buy,
            "best_sell": best_sell,
            "spread": round(spread, 2),
            "spread_ratio": round(spread_ratio, 2),
            "profit_after_tax": round(profit_after_tax, 2),
            "roc": round(roc, 2),
            "best_buy_vol": best_buy_vol,
            "best_sell_vol": best_sell_vol,
            "total_buy_vol": total_buy_vol,
            "total_sell_vol": total_sell_vol,
            "buy_order_count": len(buys),
            "sell_order_count": len(sells),
            "capital_needed": round(best_sell * min(best_sell_vol, 10), 0),
            "eve_score": eve_score,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("eve_score", ascending=False)

    # 保存
    path = DATA_DIR / output_name
    df.to_csv(path, index=False)
    elapsed = (datetime.now() - t0).total_seconds()

    print(f"\n{'='*60}")
    print(f"采集完成!")
    print(f"  总页数: {buy_pages} 买 + {sell_pages} 卖 = {total_pages}")
    print(f"  API 调用: {total_pages + (len(all_type_ids) // 1000 + 1)} 次")
    print(f"  订单总数: {len(all_buy):,} 买 + {len(all_sell):,} 卖")
    print(f"  即时倒卖机会 (spread>0): {len(rows)} 个")
    print(f"  无倒卖机会 (spread<=0): {skipped} 个")
    print(f"  保存至: {path}")
    print(f"  耗时: {elapsed:.0f} 秒")
    print(f"{'='*60}")

    # 打印 Top 10
    if not df.empty:
        print(f"\n🏆 Jita 即时倒卖 Top 10:")
        print(f"{'物品':30s} {'卖价':>10s} {'买价':>10s} {'利润':>10s} {'价差比':>8s} {'RoC':>7s} {'深度':>6s} {'评分':>5s}")
        print(f"{'-'*90}")
        for _, r in df.head(10).iterrows():
            print(f"{str(r['name'])[:28]:28s} {r['best_sell']:>10,.0f} {r['best_buy']:>10,.0f} "
                  f"{r['profit_after_tax']:>10,.0f} {r['spread_ratio']:>7.1f}% {r['roc']:>6.1f}% "
                  f"{min(r['best_buy_vol'], r['best_sell_vol']):>5,} {r['eve_score']:>5.1f}")

    return df


if __name__ == "__main__":
    run_full_scan()
