"""诊断：逐日跟踪掉头策略到底买了什么、发生了什么"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.db import get_session
from sqlalchemy import text
from datetime import date, timedelta
import numpy as np

s = get_session()
today = date(2025, 3, 3)
start_dt = today - timedelta(days=30)

# 查当天候选池
r = s.execute(text("""
    WITH t AS (
        SELECT ts_code, trade_date, close, open, high, low, vol,
               LAG(close, 1) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_close,
               close / NULLIF(LAG(close, 5) OVER (PARTITION BY ts_code ORDER BY trade_date), 0) - 1 AS ret_5d,
               vol / NULLIF(LAG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date), 0) AS vol_ratio,
               CASE WHEN high > low THEN (LEAST(open, close) - low) / (high - low) ELSE 0 END AS lower_shadow,
               CASE WHEN high > low THEN (close - low) / (high - low) ELSE 0.5 END AS close_position
        FROM daily
        WHERE trade_date BETWEEN :start_dt AND :date
    )
    SELECT ts_code, close, round(ret_5d::numeric, 4), round(vol_ratio::numeric, 2),
           round(lower_shadow::numeric, 2), round(close_position::numeric, 2)
    FROM t
    WHERE trade_date = :date
      AND ret_5d < -0.10
      AND (prev_close IS NULL OR close > prev_close * 0.90)
      AND close > 0
    ORDER BY ret_5d
    LIMIT 5
"""), {"start_dt": start_dt, "date": today}).fetchall()

print(f"日期: {today}, 候选池前5:")
for row in r:
    print(f"  {row[0]} close={row[1]} ret_5d={row[2]} vol_ratio={row[3]} shadow={row[4]} close_pos={row[5]}")

# 查这5只股票的次日收盘价
codes = [row[0] for row in r]
next_day = s.execute(text("""
    SELECT ts_code, trade_date, close 
    FROM daily 
    WHERE ts_code = ANY(:codes) 
      AND trade_date > :date 
    ORDER BY ts_code, trade_date
"""), {"codes": codes, "date": today}).fetchall()

print(f"\n次日数据:")
from collections import defaultdict
nd = defaultdict(list)
for code, dt, close in next_day:
    nd[code].append((dt, float(close)))

for code, prices in nd.items():
    if prices:
        next_close = prices[0][1]
        today_close = [r for r in r if r[0] == code][0][1] if code in [x[0] for x in r] else None
        if today_close:
            ret = (next_close / float(today_close) - 1) * 100
            print(f"  {code}: {today} close={today_close} → {prices[0][0]} close={next_close}  收益={ret:+.2f}%")
    else:
        print(f"  {code}: 次日无数据!")

# 整个候选池的平均次日收益
print(f"\n=== 统计当天所有候选的次日表现 ===")
all_candidates = s.execute(text("""
    WITH t AS (
        SELECT ts_code, trade_date, close,
               LAG(close, 1) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_close,
               close / NULLIF(LAG(close, 5) OVER (PARTITION BY ts_code ORDER BY trade_date), 0) - 1 AS ret_5d
        FROM daily
        WHERE trade_date BETWEEN :start_dt AND :date
    ),
    nxt AS (
        SELECT t.ts_code, t.close AS today_close, t.ret_5d,
               LEAD(t.close) OVER (PARTITION BY t.ts_code ORDER BY t.trade_date) AS next_close
        FROM t
        WHERE t.trade_date = :date
          AND t.ret_5d < -0.10
          AND (t.prev_close IS NULL OR t.close > t.prev_close * 0.90)
          AND t.close > 0
    )
    SELECT COUNT(*),
           ROUND(AVG(next_close / today_close - 1) * 100, 2),
           ROUND(SUM(CASE WHEN next_close > today_close THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1)
    FROM nxt
    WHERE next_close IS NOT NULL
"""), {"start_dt": start_dt, "date": today}).fetchone()

print(f"  候选数: {all_candidates[0]}, 次日均值: {all_candidates[1]}%, 胜率: {all_candidates[2]}%")

s.close()
