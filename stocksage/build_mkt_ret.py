"""Pre-compute equal-weighted market returns for IVOL calculation."""
import psycopg2, numpy as np

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password='hermes_quant_2026', dbname='stocksage_alpha')
cur = conn.cursor()

print("Streaming daily data...")
cur.execute('SELECT ts_code, trade_date, close FROM daily WHERE close IS NOT NULL ORDER BY ts_code, trade_date')

daily_ret = {}
prev_code = None
prev_close = None
count = 0

for code, dt, close in cur:
    if code != prev_code:
        prev_code = code
        prev_close = float(close)
        continue
    ret = (float(close) - prev_close) / prev_close * 100
    daily_ret.setdefault(dt, []).append(ret)
    prev_close = float(close)
    count += 1
    if count % 1000000 == 0:
        print(f"  {count:,} rows...")

print(f"  {count:,} return observations, {len(daily_ret)} days")

print("Computing daily averages...")
cur.execute('DROP TABLE IF EXISTS mkt_daily_ret')
cur.execute('CREATE TABLE mkt_daily_ret (trade_date DATE PRIMARY KEY, mkt_ret DOUBLE PRECISION)')

for dt, rets in daily_ret.items():
    cur.execute('INSERT INTO mkt_daily_ret VALUES (%s, %s)', (dt, float(np.mean(rets))))

conn.commit()

cur.execute('SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM mkt_daily_ret')
c, d1, d2 = cur.fetchone()
print(f"mkt_daily_ret: {c} days, {d1} ~ {d2}")

conn.close()
print("DONE")
