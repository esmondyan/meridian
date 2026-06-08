"""Fix: recompute only fundamental factors (ln_cap, pb, pe_ttm, roe, roa)"""
import psycopg2, numpy as np, pandas as pd, psycopg2.extras

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password='hermes_quant_2026', dbname='stocksage_alpha')
cur = conn.cursor()

FUND_COLS = ['ln_cap', 'pb', 'pe_ttm', 'roe', 'roa']

# Load daily_basic
print("Loading daily_basic...")
cur.execute('SELECT ts_code, trade_date, total_mv, pb, pe_ttm FROM daily_basic ORDER BY ts_code, trade_date')
db_df = pd.DataFrame(cur.fetchall(), columns=['ts_code','trade_date','total_mv','pb','pe_ttm'])
db_df['ln_cap'] = np.log(db_df['total_mv'].replace(0, np.nan))
db_df = db_df.set_index(['ts_code','trade_date']).sort_index()
print(f"  {len(db_df)} rows")

# Load fina_indicator
print("Loading fina_indicator...")
cur.execute('SELECT ts_code, end_date, roe, roa FROM fina_indicator ORDER BY ts_code, end_date')
fina_rows = cur.fetchall()
fina_df = pd.DataFrame(fina_rows, columns=['ts_code', 'end_date', 'roe', 'roa'])
fina_df = fina_df.set_index(['ts_code', 'end_date']).sort_index()
print(f"  {len(fina_df)} rows")

# Get stocks that have factor data but no fundamental data
print("Finding stocks to update...")
cur.execute("""
    SELECT DISTINCT ts_code FROM factor_technical_wide
    WHERE st_rev_20d IS NOT NULL AND ln_cap IS NULL AND trade_date >= '2024-06-01'
""")
codes = [r[0] for r in cur.fetchall()]
print(f"  {len(codes)} stocks need fundamental data")

if not codes:
    # Try broader search
    cur.execute("SELECT DISTINCT ts_code FROM factor_technical_wide WHERE st_rev_20d IS NOT NULL AND ln_cap IS NULL LIMIT 100")
    codes = [r[0] for r in cur.fetchall()]
    print(f"  {len(codes)} stocks (sample)")

total = 0
for ts_code in codes:
    # Get dates for this stock
    cur.execute("SELECT trade_date FROM factor_technical_wide WHERE ts_code=%s AND st_rev_20d IS NOT NULL AND ln_cap IS NULL ORDER BY trade_date", (ts_code,))
    dates = [r[0] for r in cur.fetchall()]
    if not dates:
        continue
    
    dates_series = pd.DatetimeIndex(dates)
    
    # Fundamental from daily_basic
    try:
        # xs already drops the ts_code level — no droplevel needed!
        db_stock = db_df.xs(ts_code, level='ts_code')
        # db_stock is now a DataFrame indexed by trade_date
        db_aligned = db_stock.reindex(dates_series, method='ffill')
        
        updates = []
        for i, dt in enumerate(dates):
            ln_cap_val = float(db_aligned['ln_cap'].iloc[i]) if not pd.isna(db_aligned['ln_cap'].iloc[i]) else None
            pb_val = float(db_aligned['pb'].iloc[i]) if not pd.isna(db_aligned['pb'].iloc[i]) else None
            pe_val = float(db_aligned['pe_ttm'].iloc[i]) if not pd.isna(db_aligned['pe_ttm'].iloc[i]) else None
            roe_val = None
            roa_val = None
            updates.append((ts_code, dt, ln_cap_val, pb_val, pe_val, roe_val, roa_val))
    except KeyError:
        updates = []
    
    # Overlay fina data
    try:
        fina_stock = fina_df.xs(ts_code, level='ts_code')
        fina_aligned = fina_stock.reindex(dates_series, method='ffill')
        for i, dt in enumerate(dates):
            roe_val = float(fina_aligned['roe'].iloc[i]) if not pd.isna(fina_aligned['roe'].iloc[i]) else None
            roa_val = float(fina_aligned['roa'].iloc[i]) if not pd.isna(fina_aligned['roa'].iloc[i]) else None
            if updates and i < len(updates) and updates[i][1] == dt:
                code, d, ln, pb, pe, _, _ = updates[i]
                updates[i] = (code, d, ln, pb, pe, roe_val, roa_val)
    except KeyError:
        pass
    
    if updates:
        for code, dt, ln, pb, pe, roe, roa in updates:
            cur.execute("""UPDATE factor_technical_wide SET ln_cap=%s, pb=%s, pe_ttm=%s, roe=%s, roa=%s WHERE ts_code=%s AND trade_date=%s""",
                (ln, pb, pe, roe, roa, code, dt))
        conn.commit()
        total += len(updates)
    
    if total % 50000 == 0 and total > 0:
        print(f"  {total} updates...")

print(f"Done: {total} fundamental values updated")

# Verify
cur.execute("SELECT COUNT(*) FROM factor_technical_wide WHERE ln_cap IS NOT NULL")
print(f"ln_cap non-null: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM factor_technical_wide WHERE pb IS NOT NULL")
print(f"pb non-null: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM factor_technical_wide WHERE roe IS NOT NULL")
print(f"roe non-null: {cur.fetchone()[0]}")
conn.close()
