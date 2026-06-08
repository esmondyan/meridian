"""
Build or extend fwd_returns table: precompute forward 5/10/20-day returns
from daily.close using server-side cursor + two-connection pattern.

- stream close prices per stock (server-side cursor, read conn)
- compute forward returns per stock
- batch INSERT into fwd_returns (write conn)
- EXTEND mode: only add new dates since last build
"""
import sys, os
sys.path.insert(0, '/home/hermes/projects/stocksage/alpha')
with open('/home/hermes/projects/stocksage/alpha/.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

import psycopg2
import psycopg2.extras

# Two connections: read for streaming, write for DDL/INSERT
conn_r = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')
conn_w = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')

cur_w = conn_w.cursor()

# ── Check current fwd_returns state ──
cur_w.execute("SELECT MAX(trade_date) FROM fwd_returns")
last_fwd_date = cur_w.fetchone()[0]
print(f"fwd_returns last date: {last_fwd_date}")

# Get the daily table max date
cur_w.execute("SELECT MAX(trade_date) FROM daily")
max_daily = cur_w.fetchone()[0]
print(f"daily max date: {max_daily}")

if last_fwd_date and last_fwd_date >= max_daily:
    print("fwd_returns is up to date. Nothing to do.")
    conn_r.close()
    conn_w.close()
    exit(0)

# Decide start date
if last_fwd_date:
    # Extend: re-process from START (need full stock histories to compute forward returns)
    # Forward returns need future dates, so we need all data
    start_date = '2023-12-01'
    print(f"Rebuilding from {start_date} (need full history for forward computation)")
    # Drop and recreate
    cur_w.execute("DROP TABLE IF EXISTS fwd_returns")
    conn_w.commit()
else:
    start_date = '2023-12-01'

cur_w.execute("""
    CREATE TABLE IF NOT EXISTS fwd_returns (
        ts_code VARCHAR(32),
        trade_date DATE,
        fwd_5d DOUBLE PRECISION,
        fwd_10d DOUBLE PRECISION,
        fwd_20d DOUBLE PRECISION,
        PRIMARY KEY (ts_code, trade_date)
    )
""")
conn_w.commit()

# ── Stream close prices ──
print(f"Streaming close prices from {start_date}...")
cur_r = conn_r.cursor('close_stream')
cur_r.itersize = 50000
cur_r.execute("""
    SELECT ts_code, trade_date, close FROM daily
    WHERE trade_date >= %s AND close IS NOT NULL
    ORDER BY ts_code, trade_date
""", (start_date,))

HORIZONS = [5, 10, 20]
batch = []
prev_code = None
prev_dates = []
prev_closes = []
count_stocks = 0
count_rows = 0

for code, dt, close in cur_r:
    if code != prev_code and prev_code is not None:
        # Process previous stock
        for j in range(len(prev_closes)):
            fwds = {}
            for h in HORIZONS:
                if j + h < len(prev_closes):
                    fwds[h] = (prev_closes[j + h] / prev_closes[j] - 1) * 100
            if len(fwds) == len(HORIZONS):
                batch.append((
                    prev_code, prev_dates[j],
                    fwds[5], fwds[10], fwds[20]
                ))
        
        if len(batch) >= 5000:
            psycopg2.extras.execute_values(cur_w,
                "INSERT INTO fwd_returns (ts_code, trade_date, fwd_5d, fwd_10d, fwd_20d) VALUES %s ON CONFLICT (ts_code, trade_date) DO NOTHING",
                batch, page_size=1000)
            conn_w.commit()
            count_rows += len(batch)
            batch = []
        
        count_stocks += 1
        if count_stocks % 1000 == 0:
            print(f"  {count_stocks} stocks, {count_rows:,} rows inserted...")
        
        prev_dates, prev_closes = [], []
    
    prev_code = code
    prev_dates.append(dt)
    prev_closes.append(float(close))

# Process last stock
if prev_code is not None:
    for j in range(len(prev_closes)):
        fwds = {}
        for h in HORIZONS:
            if j + h < len(prev_closes):
                fwds[h] = (prev_closes[j + h] / prev_closes[j] - 1) * 100
        if len(fwds) == len(HORIZONS):
            batch.append((
                prev_code, prev_dates[j],
                fwds[5], fwds[10], fwds[20]
            ))
    if batch:
        psycopg2.extras.execute_values(cur_w,
            "INSERT INTO fwd_returns (ts_code, trade_date, fwd_5d, fwd_10d, fwd_20d) VALUES %s ON CONFLICT (ts_code, trade_date) DO NOTHING",
            batch, page_size=1000)
        conn_w.commit()
        count_rows += len(batch)
    count_stocks += 1

print(f"Done: {count_stocks:,} stocks, {count_rows:,} rows")

# ── Add indexes ──
print("Creating indexes...")
cur_w.execute("CREATE INDEX IF NOT EXISTS idx_fwd_trade_date ON fwd_returns(trade_date)")
cur_w.execute("CREATE INDEX IF NOT EXISTS idx_fwd_code_date ON fwd_returns(ts_code, trade_date)")
conn_w.commit()

# ── Verify ──
cur_w.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM fwd_returns")
d1, d2, cnt = cur_w.fetchone()
print(f"fwd_returns: {cnt:,} rows, {d1} ~ {d2}")

conn_r.close()
conn_w.close()
print("Done building fwd_returns.")
