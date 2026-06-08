"""诊断版回测：打印每日P&L"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.db import get_session
from sqlalchemy import text
from datetime import date, timedelta
import numpy as np

TOP_N = 10
s = get_session()

dates = s.execute(text("""
    SELECT DISTINCT trade_date FROM daily
    WHERE trade_date BETWEEN '2025-01-02' AND '2025-01-31'
    ORDER BY trade_date
""")).fetchall()
trade_dates = [d[0] for d in dates]

cash = 1_000_000
holdings = {}
prev_value = 1_000_000

for i, today in enumerate(trade_dates):
    # 卖出
    if holdings:
        codes = list(holdings.keys())
        prices = s.execute(text("""
            SELECT ts_code, close FROM daily
            WHERE ts_code = ANY(:codes) AND trade_date = :date
        """), {"codes": codes, "date": today}).fetchall()
        price_map = {r[0]: float(r[1]) for r in prices}
        
        sell_value = 0
        for code, shares in holdings.items():
            if code in price_map:
                sell_value += shares * price_map[code] * 0.9985  # -0.15%
        cash += sell_value
        holdings.clear()
    
    # 买入
    start_dt = today - timedelta(days=30)
    candidates = s.execute(text("""
        WITH t AS (
            SELECT ts_code, trade_date, close, open, high, low, vol,
                   LAG(close, 1) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_close,
                   close / NULLIF(LAG(close, 5) OVER (PARTITION BY ts_code ORDER BY trade_date), 0) - 1 AS ret_5d,
                   vol / NULLIF(LAG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date), 0) AS vol_ratio,
                   CASE WHEN high > low THEN (LEAST(open, close) - low) / (high - low) ELSE 0 END AS ls,
                   CASE WHEN high > low THEN (close - low) / (high - low) ELSE 0.5 END AS cp
            FROM daily WHERE trade_date BETWEEN :start_dt AND :date
        )
        SELECT ts_code, close, COALESCE(vol_ratio, 1), ls, cp, ret_5d
        FROM t
        WHERE trade_date = :date AND ret_5d < -0.10
          AND (prev_close IS NULL OR close > prev_close * 0.90) AND close > 0
    """), {"start_dt": start_dt, "date": today}).fetchall()
    
    bought = 0
    if len(candidates) >= TOP_N:
        scored = []
        for code, close, vr, ls, cp, r5 in candidates:
            score = min(max(float(vr), 0.5), 3) * 0.30 + max(min(float(ls), 1), 0) * 0.35 + max(min(float(cp), 1), 0) * 0.35
            scored.append((code, float(close), score))
        scored.sort(key=lambda x: -x[2])
        top = scored[:TOP_N]
        
        per_stock = cash / TOP_N
        for code, price, _ in top:
            shares = int(per_stock / price)
            if shares > 0:
                cash -= shares * price * 1.0005
                holdings[code] = shares
                bought += 1
    
    # 市值
    stock_val = 0
    if holdings:
        codes = list(holdings.keys())
        prices = s.execute(text("""
            SELECT ts_code, close FROM daily
            WHERE ts_code = ANY(:codes) AND trade_date = :date
        """), {"codes": codes, "date": today}).fetchall()
        price_map = {r[0]: float(r[1]) for r in prices}
        stock_val = sum(holdings[c] * price_map.get(c, 0) for c in holdings)
    
    total = cash + stock_val
    daily_ret = (total / prev_value - 1) * 100 if prev_value > 0 else 0
    prev_value = total
    
    print(f"{today}  买入{bought}只  cash={cash:,.0f}  stock={stock_val:,.0f}  total={total:,.0f}  ({daily_ret:+.2f}%)")

s.close()
