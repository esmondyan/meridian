"""
批量计算策略衍生因子，复用 store_factor_wide 进行高效 UPSERT。
"""
import sys, os
sys.path.insert(0, '/home/hermes/projects/stocksage/alpha')

import pandas as pd
import numpy as np
from sqlalchemy import text
from src.data.db import get_engine
from src.factors.technical import store_factor_wide
from src.factors.strategy import (
    calc_is_limit_up, calc_days_since_last_limit_up,
    calc_pullback_from_limit_up, calc_yang_today, calc_yin_streak,
    calc_yang_after_yin_streak, calc_ma10_ma54_platform,
)

engine = get_engine()

STOCK_FACTORS = [
    'limit_up_flag', 'days_since_limit_up', 'pullback_from_limit_up',
    'yang_today', 'yin_streak', 'yang_after_yin_streak',
    'ma10_ma54_platform',
]

# ── Get stocks ──
with engine.connect() as conn:
    result = conn.execute(text("SELECT DISTINCT ts_code FROM daily ORDER BY ts_code"))
    codes = [row[0] for row in result]
# Deduplicate: keep shorter code (e.g. '000001' not '000001.SZ')
seen = set()
unique_codes = []
for c in codes:
    base = c.split('.')[0]
    if base not in seen:
        seen.add(base)
        unique_codes.append(c)
codes = unique_codes
print(f"{len(codes)} unique stocks to process")

# ── Batch compute and upsert ──
batch_size = 50
total_rows = 0

for batch_start in range(0, len(codes), batch_size):
    batch_codes = codes[batch_start:batch_start + batch_size]
    
    with engine.connect() as conn:
        placeholders = ','.join([f"'{c}'" for c in batch_codes])
        df = pd.read_sql(f"""
            SELECT ts_code, trade_date, "open", high, low, close, pct_change
            FROM daily
            WHERE ts_code IN ({placeholders})
            ORDER BY ts_code, trade_date
        """, conn)
    
    if df.empty:
        continue
    
    for code, group in df.groupby('ts_code'):
        group = group.sort_values('trade_date').reset_index(drop=True)
        
        if len(group) < 55:
            continue
        
        result = pd.DataFrame({
            'ts_code': code,
            'trade_date': group['trade_date'],
            'limit_up_flag': calc_is_limit_up(group['pct_change']),
            'days_since_limit_up': calc_days_since_last_limit_up(group['pct_change']),
            'pullback_from_limit_up': calc_pullback_from_limit_up(group['pct_change'], group['close']),
            'yang_today': calc_yang_today(group['close'], group['open']),
            'yin_streak': calc_yin_streak(group['close'], group['open']),
            'yang_after_yin_streak': calc_yang_after_yin_streak(group['close'], group['open']),
            'ma10_ma54_platform': calc_ma10_ma54_platform(group['close'], group['high'], group['low']),
        })
        # UPSERT per stock to keep memory low
        store_factor_wide(engine, result, 'factor_technical_wide')
        total_rows += len(result)
        del result
    
    elapsed_pct = (batch_start + batch_size) / len(codes) * 100
    print(f"  [{batch_start + len(batch_codes)}/{len(codes)}] {total_rows} rows upserted ({min(elapsed_pct, 100):.0f}%)")

print(f"\nDone: {total_rows} total rows updated")
print("Strategy factors added to factor_technical_wide:")
for f in STOCK_FACTORS:
    print(f"  - {f}")
