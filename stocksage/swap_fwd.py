import psycopg2, os
with open('/home/hermes/projects/stocksage/alpha/.env') as f:
    for l in f:
        if '=' in l and not l.startswith('#'):
            k,v = l.strip().split('=',1)
            os.environ[k.strip()]=v.strip()

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes', password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')
conn.autocommit = True
cur = conn.cursor()
cur.execute("DROP TABLE IF EXISTS fwd_returns CASCADE")
cur.execute("ALTER TABLE fwd_returns2 RENAME TO fwd_returns")
cur.execute("CREATE INDEX IF NOT EXISTS idx_fr_date ON fwd_returns(trade_date)")
print("Swapped: fwd_returns2 → fwd_returns")
