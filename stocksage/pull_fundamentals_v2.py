"""
Pull Tushare daily_basic and fina_indicator properly:
- daily_basic: pull by month (each month's last trading day) → ~60 calls
- fina_indicator: pull by stock batch + quarter → iterate stocks
"""
import os, sys, time
sys.path.insert(0, '/home/hermes/projects/stocksage/alpha')
with open('/home/hermes/projects/stocksage/alpha/.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

import tushare as ts
import psycopg2
import psycopg2.extras
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password='hermes_quant_2026', dbname='stocksage_alpha')
cur = conn.cursor()

# ═══════════════════════════════════════
# 1. daily_basic — pull by month-end dates
# ═══════════════════════════════════════
print("=== 1. daily_basic (month-end snapshots) ===")

# Ensure table
cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_basic (
        ts_code VARCHAR(10), trade_date DATE,
        total_mv DOUBLE PRECISION, circ_mv DOUBLE PRECISION,
        pe DOUBLE PRECISION, pe_ttm DOUBLE PRECISION, pb DOUBLE PRECISION,
        PRIMARY KEY (ts_code, trade_date)
    )
""")
conn.commit()

# Generate month-end trading dates (approximate)
dates_to_pull = []
d = date(2020, 1, 1)
while d <= date(2026, 6, 8):
    # Last day of month
    next_month = d.replace(day=28) + timedelta(days=4)
    last_day = next_month - timedelta(days=next_month.day)
    if last_day > date(2026, 6, 8):
        last_day = date(2026, 6, 8)
    if last_day >= date(2020, 1, 1):
        dates_to_pull.append(last_day)
    d = (d.replace(day=1) + relativedelta(months=1))

# Deduplicate and sort
dates_to_pull = sorted(set(dates_to_pull))
print(f"  Pulling {len(dates_to_pull)} month-end dates...")

total_db = 0
for i, dt in enumerate(dates_to_pull):
    dt_str = dt.strftime('%Y%m%d')
    
    # Check if already have this date
    cur.execute("SELECT COUNT(*) FROM daily_basic WHERE trade_date=%s", (dt,))
    if cur.fetchone()[0] > 100:
        continue
    
    time.sleep(0.3)
    try:
        df = pro.daily_basic(trade_date=dt_str,
            fields='ts_code,total_mv,circ_mv,pe,pe_ttm,pb')
    except Exception as e:
        print(f"    {dt_str}: error {e}")
        continue
    
    if df is None or len(df) == 0:
        continue
    
    batch = []
    for _, row in df.iterrows():
        try:
            batch.append((
                row['ts_code'], dt,
                float(row['total_mv']) if pd.notna(row.get('total_mv')) else None,
                float(row['circ_mv']) if pd.notna(row.get('circ_mv')) else None,
                float(row['pe']) if pd.notna(row.get('pe')) else None,
                float(row['pe_ttm']) if pd.notna(row.get('pe_ttm')) else None,
                float(row['pb']) if pd.notna(row.get('pb')) else None,
            ))
        except (ValueError, TypeError):
            continue
    
    if batch:
        # Need pandas for pd.notna
        import pandas as pd
        psycopg2.extras.execute_values(cur,
            "INSERT INTO daily_basic VALUES %s ON CONFLICT (ts_code, trade_date) DO UPDATE SET total_mv=EXCLUDED.total_mv, circ_mv=EXCLUDED.circ_mv, pe=EXCLUDED.pe, pe_ttm=EXCLUDED.pe_ttm, pb=EXCLUDED.pb",
            batch, page_size=500)
        conn.commit()
        total_db += len(batch)
        if (i+1) % 10 == 0:
            print(f"  [{i+1}/{len(dates_to_pull)}] {dt_str}: +{len(batch):,} (total {total_db:,})")

print(f"  Total daily_basic: {total_db:,} rows")

cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_basic")
d1, d2, cnt = cur.fetchone()
print(f"  Final: {cnt:,} rows, {d1} ~ {d2}")

# ═══════════════════════════════════════
# 2. fina_indicator — pull by stock batch
# ═══════════════════════════════════════
print("\n=== 2. fina_indicator (by stock, quarterly) ===")

cur.execute("SELECT ts_code FROM stock_basic WHERE delist_date IS NULL ORDER BY ts_code")
all_codes = [r[0] for r in cur.fetchall()]

# We already have 2024Q3-2025Q1. Need 2020Q1-2024Q2 and 2025Q2+
quarters = []
for y in range(2020, 2027):
    for q, (m, d) in enumerate([(3,31), (6,30), (9,30), (12,31)], 1):
        q_date = date(y, m, d)
        if q_date > date(2026, 6, 8):
            break
        if q_date < date(2020, 1, 1):
            continue
        # Skip if already covered
        cur.execute("SELECT COUNT(*) FROM fina_indicator WHERE end_date=%s", (q_date,))
        if cur.fetchone()[0] > 100:
            continue
        quarters.append(q_date)

print(f"  Need data for {len(quarters)} quarters")

total_fina = 0
BATCH_SIZE = 50  # stocks per API call

for q_date in sorted(quarters):
    period_str = q_date.strftime('%Y%m%d')
    q_total = 0
    
    for offset in range(0, len(all_codes), BATCH_SIZE):
        batch_codes = all_codes[offset:offset+BATCH_SIZE]
        ts_code_str = ','.join(batch_codes)
        
        time.sleep(0.4)
        try:
            df = pro.fina_indicator(ts_code=ts_code_str, period=period_str,
                fields='ts_code,end_date,roe,roa')
        except Exception as e:
            if q_total == 0:
                print(f"    {period_str}: error {e}")
            break
        
        if df is None or len(df) == 0:
            break
        
        batch = []
        for _, row in df.iterrows():
            try:
                ed = date(int(row['end_date'][:4]), int(row['end_date'][4:6]), int(row['end_date'][6:]))
                batch.append((
                    row['ts_code'], ed,
                    float(row['roe']) if pd.notna(row.get('roe')) else None,
                    float(row['roa']) if pd.notna(row.get('roa')) else None,
                ))
            except (ValueError, TypeError):
                continue
            import pandas as pd
        
        if batch:
            psycopg2.extras.execute_values(cur,
                "UPDATE fina_indicator SET roe=data.roe, roa=data.roa FROM (VALUES %s) AS data(ts_code, end_date, roe, roa) WHERE fina_indicator.ts_code=data.ts_code AND fina_indicator.end_date=data.end_date",
                batch, page_size=500)
            conn.commit()
            q_total += len(batch)
    
    if q_total > 0:
        total_fina += q_total
        print(f"  {period_str}: {q_total:,} stocks")

print(f"  Total updated: {total_fina:,}")

cur.execute("SELECT MIN(end_date), MAX(end_date), COUNT(*) FROM fina_indicator")
fd1, fd2, fcnt = cur.fetchone()
print(f"  Final: {fcnt:,} rows, {fd1} ~ {fd2}")

conn.close()
print("\n=== DONE ===")
