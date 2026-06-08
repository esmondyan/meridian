import psycopg2, os
with open('/home/hermes/projects/stocksage/alpha/.env') as f:
    for l in f:
        if '=' in l and not l.startswith('#'):
            k,v = l.strip().split('=',1)
            os.environ[k.strip()]=v.strip()
conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes', password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')
conn.autocommit = True
cur = conn.cursor()
cur.execute('CREATE INDEX IF NOT EXISTS idx_ftw_date ON factor_technical_wide(trade_date)')
print('ftw index ok')
cur.execute('CREATE INDEX IF NOT EXISTS idx_fr_date ON fwd_returns(trade_date)')
print('fr index ok')
print('done')
