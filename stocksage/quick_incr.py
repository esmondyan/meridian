"""Targeted incremental: only fetch stocks missing data for recent trading days."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import akshare as ak
import pandas as pd
from sqlalchemy import text
from tqdm import tqdm

from src.data.db import get_engine
from src.data.models import Daily

engine = get_engine()

# ── Find which stocks are missing data for recent dates ──
end = date.today()
start = end - timedelta(days=10)

with engine.connect() as conn:
    # All stocks
    all_codes = conn.execute(text(
        "SELECT ts_code, symbol FROM stock_basic ORDER BY ts_code"
    )).fetchall()
    
    # Existing data for the date range
    existing = conn.execute(text(
        "SELECT ts_code, trade_date::text FROM daily "
        "WHERE trade_date >= :sd AND trade_date <= :ed"
    ), {'sd': start.strftime('%Y-%m-%d'), 'ed': end.strftime('%Y-%m-%d')}).fetchall()
    existing_set = set((r[0], r[1]) for r in existing)

# Map ts_code to 6-digit symbol and full AKShare symbol
code_map = {}
for ts_code, symbol in all_codes:
    if ts_code.endswith('.SH'):
        full = f'sh{symbol}'
    elif ts_code.endswith('.SZ'):
        full = f'sz{symbol}'
    elif ts_code.endswith('.BJ'):
        full = f'bj{symbol}'
    else:
        continue
    code_map[ts_code] = (symbol, full)

# Check which codes have at least one missing date in range
# We'll fetch the full range and let the DB dedup
missing = []
for ts_code, (symbol, full) in code_map.items():
    missing.append({'ts_code': ts_code, 'symbol': symbol, 'full': full})

print(f"🔄 增量补缺 | {len(missing)} stocks | {start}~{end}")
print(f"📋 已有 {len(existing_set):,} 条，仅插入新数据")

def fetch_one(info):
    symbol, full = info['symbol'], info['full']
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_daily(
                symbol=full,
                start_date=start.strftime('%Y-%m-%d'),
                end_date=end.strftime('%Y-%m-%d'),
                adjust='qfq'
            )
            if df is not None and not df.empty:
                res = pd.DataFrame({
                    'ts_code': symbol,
                    'trade_date': pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d'),
                    'open': df['open'].values,
                    'close': df['close'].values,
                    'high': df['high'].values,
                    'low': df['low'].values,
                    'vol': df['volume'].values,
                    'amount': df['amount'].values,
                })
                # Filter out existing
                mask = ~res.apply(lambda row: (row['ts_code'], row['trade_date']) in existing_set, axis=1)
                res = res[mask]
                if not res.empty:
                    return ('ok', res)
                return ('skip', None)
            return ('empty', None)
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(1)
            else:
                return ('error', symbol, str(e))
    return ('empty', None)

success, skipped, failed = 0, 0, 0
written = 0

with ThreadPoolExecutor(max_workers=6) as ex:
    futures = {ex.submit(fetch_one, c): c for c in missing}
    for f in tqdm(as_completed(futures), total=len(futures), desc="补缺", unit="股"):
        r = f.result()
        if r[0] == 'ok':
            success += 1
            with engine.begin() as conn:
                r[1].to_sql('daily', conn, if_exists='append', index=False)
                written += len(r[1])
        elif r[0] == 'skip':
            skipped += 1
        elif r[0] == 'error':
            failed += 1

print(f"\n✅ 成功: {success} | ⏭️ 跳过: {skipped} | ❌ 失败: {failed}")
print(f"📝 新增行数: {written}")
