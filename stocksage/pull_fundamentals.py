"""
Pull Tushare fundamental data for factor computation.
- stock_basic: add total_shares column
- daily_basic: create table with daily PE, PB, total_mv, circ_mv
- fina_indicator: extend to 2020+
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
from datetime import date

ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password='hermes_quant_2026', dbname='stocksage_alpha')
cur = conn.cursor()

# ═══════════════════════════════════════
# 1. daily_basic — daily fundamentals
# ═══════════════════════════════════════
print("=== 1. daily_basic: creating and filling ===")
cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_basic (
        ts_code VARCHAR(10),
        trade_date DATE,
        total_mv DOUBLE PRECISION,
        circ_mv DOUBLE PRECISION,
        pe DOUBLE PRECISION,
        pe_ttm DOUBLE PRECISION,
        pb DOUBLE PRECISION,
        PRIMARY KEY (ts_code, trade_date)
    )
""")
conn.commit()
print("  Table ready")

# Check what we already have
cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_basic")
d1, d2, cnt = cur.fetchone()
print(f"  Existing: {cnt:,} rows, {d1} ~ {d2}")

# Pull in chunks (Tushare limits to ~5000 rows per call, ~1 month at a time)
start_dates = []
d = date(2020, 1, 1)
while d <= date(2026, 6, 1):
    if d.month <= 6:
        start_dates.append(date(d.year, 1, 1))
        start_dates.append(date(d.year, 7, 1))
    d = date(d.year + 1, 1, 1)

total_new = 0
for start in start_dates:
    end = date(start.year + (0 if start.month == 1 else 0), 
              6 if start.month == 1 else 12, 
              30 if start.month == 1 else 31)
    if end > date(2026, 6, 8):
        end = date(2026, 6, 8)
    
    # Check if this period is already covered
    str_start = start.isoformat()
    str_end = end.isoformat()
    
    print(f"  Fetching {str_start} ~ {str_end}...", end=' ', flush=True)
    time.sleep(0.3)
    
    try:
        df = pro.daily_basic(
            trade_date=str_start.replace('-',''),
            start_date=str_start.replace('-',''),
            end_date=str_end.replace('-',''),
            fields='ts_code,trade_date,total_mv,circ_mv,pe,pe_ttm,pb'
        )
    except Exception as e:
        print(f"Error: {e}")
        continue
    
    if df is None or len(df) == 0:
        print("no data")
        continue
    
    batch = []
    for _, row in df.iterrows():
        batch.append((
            row['ts_code'],
            date(int(row['trade_date'][:4]), int(row['trade_date'][4:6]), int(row['trade_date'][6:])),
            float(row['total_mv']) if row['total_mv'] and row['total_mv'] != 'None' else None,
            float(row['circ_mv']) if row['circ_mv'] and row['circ_mv'] != 'None' else None,
            float(row['pe']) if row['pe'] and row['pe'] != 'None' else None,
            float(row['pe_ttm']) if row['pe_ttm'] and row['pe_ttm'] != 'None' else None,
            float(row['pb']) if row['pb'] and row['pb'] != 'None' else None,
        ))
    
    psycopg2.extras.execute_values(cur,
        "INSERT INTO daily_basic VALUES %s ON CONFLICT (ts_code, trade_date) DO UPDATE SET total_mv=EXCLUDED.total_mv, circ_mv=EXCLUDED.circ_mv, pe=EXCLUDED.pe, pe_ttm=EXCLUDED.pe_ttm, pb=EXCLUDED.pb",
        batch, page_size=500)
    conn.commit()
    total_new += len(batch)
    print(f"{len(batch):,} rows")

print(f"  Total new: {total_new:,}")

# Verify
cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_basic")
d1, d2, cnt = cur.fetchone()
print(f"  Final: {cnt:,} rows, {d1} ~ {d2}")

# ═══════════════════════════════════════
# 2. fina_indicator — extend to 2020+
# ═══════════════════════════════════════
print("\n=== 2. fina_indicator: extending history ===")
cur.execute("SELECT MIN(end_date), MAX(end_date) FROM fina_indicator")
fd1, fd2 = cur.fetchone()
print(f"  Existing: {fd1} ~ {fd2}")

# Pull annual + quarterly data from 2020
years = list(range(2020, 2027))
total_fina = 0
for y in years:
    for q in [1, 2, 3, 4]:
        period = f"{y}{q:02d}31"
        if q == 2: period = f"{y}0630"
        elif q == 3: period = f"{y}0930"
        elif q == 4: period = f"{y}1231"
        
        # Skip if already covered
        cur.execute("SELECT COUNT(*) FROM fina_indicator WHERE end_date=%s", (date(y, q*3 if q<4 else 12, 1).replace(day=1),))
        # Actually just use period date
        try:
            end_d = date(y, q*3, 31) if q < 4 else date(y, 12, 31)
        except:
            end_d = date(y, q*3, 30) if q < 4 else date(y, 12, 31)
        
        cur.execute("SELECT COUNT(*) FROM fina_indicator WHERE end_date=%s", (end_d,))
        if cur.fetchone()[0] > 100:
            continue
        
        print(f"  Fetching {period}...", end=' ', flush=True)
        time.sleep(0.4)
        
        try:
            df = pro.fina_indicator(period=period,
                fields='ts_code,end_date,eps,roe,roa,gross_margin,net_margin,revenue_yoy,profit_yoy,debt_ratio,eps_yoy,bps,cf_ps')
        except Exception as e:
            print(f"Error: {e}")
            continue
        
        if df is None or len(df) == 0:
            print("no data")
            continue
        
        batch = []
        for _, row in df.iterrows():
            try:
                batch.append((
                    row['ts_code'],
                    date(int(row['end_date'][:4]), int(row['end_date'][4:6]), int(row['end_date'][6:])),
                    None,  # ann_date
                    float(row['eps']) if row['eps'] and row['eps'] != 'None' else None,
                    float(row['roe']) if row['roe'] and row['roe'] != 'None' else None,
                    float(row['roa']) if row['roa'] and row['roa'] != 'None' else None,
                    float(row['gross_margin']) if row['gross_margin'] and row['gross_margin'] != 'None' else None,
                    float(row['net_margin']) if row['net_margin'] and row['net_margin'] != 'None' else None,
                    float(row['revenue_yoy']) if row['revenue_yoy'] and row['revenue_yoy'] != 'None' else None,
                    float(row['profit_yoy']) if row['profit_yoy'] and row['profit_yoy'] != 'None' else None,
                    float(row['debt_ratio']) if row['debt_ratio'] and row['debt_ratio'] != 'None' else None,
                    float(row['eps_yoy']) if row['eps_yoy'] and row['eps_yoy'] != 'None' else None,
                    float(row['bps']) if row['bps'] and row['bps'] != 'None' else None,
                    float(row['cf_ps']) if row['cf_ps'] and row['cf_ps'] != 'None' else None,
                ))
            except (ValueError, TypeError):
                continue
        
        psycopg2.extras.execute_values(cur,
            "INSERT INTO fina_indicator (ts_code,end_date,ann_date,eps,roe,roa,gross_margin,net_margin,revenue_yoy,profit_yoy,debt_ratio,eps_yoy,bps,cf_ps) VALUES %s ON CONFLICT DO NOTHING",
            batch, page_size=500)
        conn.commit()
        total_fina += len(batch)
        print(f"{len(batch):,} rows")

print(f"  Total new fina: {total_fina:,}")

cur.execute("SELECT MIN(end_date), MAX(end_date), COUNT(*) FROM fina_indicator")
fd1, fd2, fcnt = cur.fetchone()
print(f"  Final: {fcnt:,} rows, {fd1} ~ {fd2}")

conn.close()
print("\n=== DONE ===")
