"""
Compute 10 new factors and write to factor_technical_wide.
- 5 OHLCV-based: st_rev_20d, max_ret_20d, ivol_20d, mom_12m_1m, amihud_illiq
- 5 fundamental: ln_cap, pb, pe_ttm, roe, roa (graceful degradation if no data)

Pattern: per-stock computation via pandas, batch UPSERT into factor_technical_wide.
Auto-adds new columns to the wide table.
"""
import os, sys
sys.path.insert(0, '/home/hermes/projects/stocksage/alpha')

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from datetime import date, timedelta

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password='hermes_quant_2026', dbname='stocksage_alpha')
cur = conn.cursor()

# ── Config ──
RECENT_DAYS = 500  # days of history per stock
NEW_COLS = [
    'st_rev_20d', 'max_ret_20d', 'ivol_20d', 'mom_12m_1m', 'amihud_illiq',
    'ln_cap', 'pb', 'pe_ttm', 'roe', 'roa',
]

# Check which fundamental tables exist
cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='daily_basic')")
has_daily_basic = cur.fetchone()[0]
cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='fina_indicator')")
has_fina = cur.fetchone()[0]
print(f"daily_basic: {'✅' if has_daily_basic else '❌'}")
print(f"fina_indicator: {'✅' if has_fina else '❌'}")

# ── Auto-migrate columns ──
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='factor_technical_wide'")
existing = {r[0] for r in cur.fetchall()}
for col in NEW_COLS:
    if col not in existing:
        cur.execute(f'ALTER TABLE factor_technical_wide ADD COLUMN "{col}" DOUBLE PRECISION')
        print(f"  ➕ added column: {col}")
conn.commit()

# ── Get all active stocks ──
cur.execute("SELECT ts_code FROM stock_basic WHERE delist_date IS NULL ORDER BY ts_code")
all_codes = [r[0] for r in cur.fetchall()]
print(f"\nProcessing {len(all_codes)} stocks...")

# ── Pre-load fundamental data if available ──
daily_basic_df = None
if has_daily_basic:
    print("Loading daily_basic...")
    cur.execute("SELECT ts_code, trade_date, total_mv, pb, pe_ttm FROM daily_basic ORDER BY ts_code, trade_date")
    rows = cur.fetchall()
    if rows:
        daily_basic_df = pd.DataFrame(rows, columns=['ts_code', 'trade_date', 'total_mv', 'pb', 'pe_ttm'])
        # Add ln_cap
        daily_basic_df['ln_cap'] = np.log(daily_basic_df['total_mv'].replace(0, np.nan))
        daily_basic_df = daily_basic_df.set_index(['ts_code', 'trade_date'])
        print(f"  {len(daily_basic_df):,} rows")
    else:
        has_daily_basic = False

fina_df = None
if has_fina:
    print("Loading fina_indicator...")
    cur.execute("SELECT ts_code, end_date, roe, roa FROM fina_indicator ORDER BY ts_code, end_date")
    rows = cur.fetchall()
    if rows:
        fina_df = pd.DataFrame(rows, columns=['ts_code', 'end_date', 'roe', 'roa'])
        fina_df = fina_df.set_index(['ts_code', 'end_date'])
        print(f"  {len(fina_df):,} rows")
    else:
        has_fina = False

# ── Pre-compute market returns for IVOL ──
print("Computing market returns (equal-weighted)...")
cur.execute("""
    SELECT trade_date, AVG((close - LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date)) / NULLIF(LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date), 0)) * 100
    FROM daily
    WHERE close IS NOT NULL
    GROUP BY trade_date
    ORDER BY trade_date
""")
mkt_ret = {}
for dt, avg_ret in cur:
    if avg_ret is not None:
        mkt_ret[dt] = float(avg_ret)
print(f"  {len(mkt_ret)} trading days")

# ── Per-stock computation ──
total_rows = 0
stocks_done = 0

for ts_code in all_codes:
    # Load daily data
    cur.execute("""
        SELECT trade_date, open, high, low, close, vol, amount
        FROM daily WHERE ts_code=%s AND close IS NOT NULL
        ORDER BY trade_date
    """, (ts_code,))
    rows = cur.fetchall()
    if len(rows) < 60:
        continue
    
    df = pd.DataFrame(rows, columns=['trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount'])
    df = df.set_index('trade_date').sort_index()
    
    # Only keep recent days
    if len(df) > RECENT_DAYS:
        df = df.tail(RECENT_DAYS)
    
    close = df['close'].astype(float)
    vol = df['vol'].astype(float)
    amount = df['amount'].astype(float)
    
    result = pd.DataFrame(index=df.index)
    
    # ── OHLCV factors ──
    # Short-term reversal (20-day return, negative sign: reversal)
    result['st_rev_20d'] = -(close.pct_change(20) * 100)
    
    # MAX effect: max daily return in past 20 days
    ret_1d = close.pct_change() * 100
    result['max_ret_20d'] = ret_1d.rolling(20).max()
    
    # 12-1 month momentum (t-250 to t-20 return)
    result['mom_12m_1m'] = (close.shift(20) / close.shift(250) - 1) * 100
    
    # Amihud illiquidity: avg(|return| / amount) * 10^8
    result['amihud_illiq'] = (
        (ret_1d.abs() / amount.replace(0, np.nan))
        .rolling(20).mean() * 1e8
    )
    
    # IVOL: std of residual from market model, 20-day rolling
    # Simplified: std(stock_ret - mkt_ret) over 20 days
    mkt_ret_series = pd.Series({dt: mkt_ret.get(dt, np.nan) for dt in df.index})
    excess_ret = ret_1d - mkt_ret_series
    # Rolling residual vol: sqrt(var(excess) - market adjustment)
    result['ivol_20d'] = excess_ret.rolling(20).std()
    
    # ── Fundamental factors ──
    if has_daily_basic:
        try:
            db_data = daily_basic_df.xs(ts_code, level='ts_code', drop_level=False)
            db_data = db_data.droplevel('ts_code')
            # Reindex to match daily dates
            db_aligned = db_data.reindex(df.index, method='ffill')
            result['ln_cap'] = db_aligned['ln_cap']
            result['pb'] = db_aligned['pb']
            result['pe_ttm'] = db_aligned['pe_ttm']
        except KeyError:
            pass
    
    if has_fina:
        try:
            fina_data = fina_df.xs(ts_code, level='ts_code', drop_level=False)
            fina_data = fina_data.droplevel('ts_code')
            # Forward-fill quarterly data to daily
            # Create daily date range and fill
            fina_reindexed = fina_data.reindex(df.index, method='ffill')
            result['roe'] = fina_reindexed['roe']
            result['roa'] = fina_reindexed['roa']
        except KeyError:
            pass
    
    # Drop NaN rows
    result = result.dropna(how='all')
    if len(result) == 0:
        continue
    
    # Build insert batch
    batch = []
    for idx, row in result.iterrows():
        vals = [ts_code, idx]
        all_none = True
        for col in NEW_COLS:
            v = row.get(col)
            if pd.notna(v):
                vals.append(float(v))
                all_none = False
            else:
                vals.append(None)
        if not all_none:
            batch.append(tuple(vals))
    
    if batch:
        cols_sql = ', '.join(['ts_code', 'trade_date'] + [f'"{c}"' for c in NEW_COLS])
        placeholders = ', '.join(['%s'] * (2 + len(NEW_COLS)))
        updates = ', '.join([f'"{c}" = EXCLUDED."{c}"' for c in NEW_COLS])
        
        cur2 = conn.cursor()
        psycopg2.extras.execute_values(cur2,
            f"INSERT INTO factor_technical_wide ({cols_sql}) VALUES %s ON CONFLICT (ts_code, trade_date) DO UPDATE SET {updates}",
            batch, page_size=500)
        conn.commit()
        cur2.close()
        total_rows += len(batch)
    
    stocks_done += 1
    if stocks_done % 500 == 0:
        print(f"  {stocks_done}/{len(all_codes)} stocks, {total_rows:,} rows written...")

print(f"\nDone: {stocks_done} stocks, {total_rows:,} new factor rows")
conn.close()
