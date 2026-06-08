import psycopg2, os, time
with open('/home/hermes/projects/stocksage/alpha/.env') as f:
    for l in f:
        if '=' in l and not l.startswith('#'):
            k,v = l.strip().split('=',1)
            os.environ[k.strip()]=v.strip()

t0 = time.time()
conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes', password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')
print(f"connect: {time.time()-t0:.1f}s")

t0 = time.time()
cur = conn.cursor()
cur.execute("SELECT 1")
print(f"select 1: {time.time()-t0:.1f}s, result={cur.fetchone()}")

t0 = time.time()
cur.execute("SELECT COUNT(*) FROM fwd_returns")
print(f"count fwd: {time.time()-t0:.1f}s, result={cur.fetchone()}")

t0 = time.time()
cur.execute("SELECT DISTINCT trade_date FROM fwd_returns ORDER BY trade_date LIMIT 5")
print(f"distinct lim5: {time.time()-t0:.1f}s, result={cur.fetchall()[:3]}")

t0 = time.time()
cur.execute("SELECT DISTINCT trade_date FROM fwd_returns ORDER BY trade_date")
rows = cur.fetchall()
print(f"distinct all: {time.time()-t0:.1f}s, count={len(rows)}")

t0 = time.time()
cur.execute("""
    SELECT f.price_position, fr.fwd_5d, fr.fwd_10d, fr.fwd_20d
    FROM factor_technical_wide f
    JOIN fwd_returns fr ON f.ts_code = fr.ts_code AND f.trade_date = fr.trade_date
    WHERE f.trade_date = %s AND f.price_position IS NOT NULL
""", (rows[0][0],))
data = cur.fetchall()
print(f"join 1 date: {time.time()-t0:.1f}s, rows={len(data)}")
print("ALL OK")
