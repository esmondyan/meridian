"""
Add ROE/ROA from fina_indicator to factor_technical_wide, then run IC on all 10 new factors.
"""
import psycopg2, numpy as np, sys

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password='hermes_quant_2026', dbname='stocksage_alpha')
cur = conn.cursor()

# ═══ Step 1: Add ROE/ROA columns ═══
for col in ['roe', 'roa']:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='factor_technical_wide' AND column_name='%s'" % col)
    if not cur.fetchone():
        cur.execute('ALTER TABLE factor_technical_wide ADD COLUMN \"%s\" DOUBLE PRECISION' % col)
        print('+col: %s' % col)
conn.commit()

# Load fina_indicator
print("Loading fina_indicator...")
cur.execute('SELECT ts_code, end_date, roe, roa FROM fina_indicator ORDER BY ts_code, end_date')
fina_rows = cur.fetchall()
print("  %d rows" % len(fina_rows))

# Build per-stock dict: {ts_code: [(end_date, roe, roa), ...]}
fina_data = {}
for ts, ed, roe, roa in fina_rows:
    fina_data.setdefault(ts, []).append((ed, roe, roa))

print("  %d stocks have fina data" % len(fina_data))

# Update factor_technical_wide with ROE/ROA via forward-fill
print("Updating factor_technical_wide with ROE/ROA...")
total = 0
for ts_code, quarters in fina_data.items():
    # Get dates that need ROE/ROA
    cur.execute("SELECT trade_date FROM factor_technical_wide WHERE ts_code=%s AND roe IS NULL ORDER BY trade_date", (ts_code,))
    dates = [r[0] for r in cur.fetchall()]
    if not dates:
        continue
    
    # Forward-fill: for each date, find most recent quarter end_date <= trade_date
    quarters_sorted = sorted(quarters, key=lambda x: x[0])
    updates = []
    for dt in dates:
        # Find last quarter before this date
        best = None
        for qd, roe, roa in quarters_sorted:
            if qd <= dt:
                best = (roe, roa)
        if best:
            updates.append((dt, best[0], best[1]))
    
    if updates:
        for dt, roe, roa in updates:
            cur.execute("UPDATE factor_technical_wide SET roe=%s, roa=%s WHERE ts_code=%s AND trade_date=%s",
                (float(roe) if roe else None, float(roa) if roa else None, ts_code, dt))
        conn.commit()
        total += len(updates)
    
    if total % 100000 == 0 and total > 0:
        print("  %d updates..." % total)

print("  %d ROE/ROA values updated" % total)

# ═══ Step 2: IC Analysis ═══
print("\n=== IC Analysis ===")

FACTORS = [
    'st_rev_20d', 'max_ret_20d', 'ivol_20d', 'mom_12m_1m', 'amihud_illiq',
    'ln_cap', 'pb', 'pe_ttm', 'roe', 'roa',
]

# Use existing fwd_returns
cur.execute("SELECT MAX(trade_date) FROM fwd_returns")
max_fwd = cur.fetchone()[0]
print("fwd_returns max date: %s" % max_fwd)

# Get dates with good overlap
cur.execute("""
    SELECT f.trade_date FROM factor_technical_wide f
    JOIN fwd_returns fr ON f.ts_code=fr.ts_code AND f.trade_date=fr.trade_date
    WHERE f.st_rev_20d IS NOT NULL AND f.trade_date >= '2023-01-01'
    GROUP BY f.trade_date HAVING COUNT(*) >= 100
    ORDER BY f.trade_date
""")
dates = [r[0] for r in cur.fetchall()]
print("%d dates with >=100 stocks overlap" % len(dates))

# Sample every 20th date for speed
sample_dates = dates[::20]
if len(sample_dates) < 5:
    sample_dates = dates
sample_dates = sample_dates[:30]  # max 30
print("Using %d sample dates" % len(sample_dates))

def spearman_r(x, y):
    n = len(x)
    if n < 30:
        return 0
    rx = np.argsort(np.argsort(x)).astype(float) + 1
    ry = np.argsort(np.argsort(y)).astype(float) + 1
    return 1 - 6 * np.sum((rx - ry)**2) / (n * (n*n - 1))

results = []
for factor in FACTORS:
    ics = []
    for dt in sample_dates:
        cur.execute("""
            SELECT f.\"%s\"::float, fr.fwd_5d::float
            FROM factor_technical_wide f
            JOIN fwd_returns fr ON f.ts_code=fr.ts_code AND f.trade_date=fr.trade_date
            WHERE f.trade_date=%%s AND f.\"%s\" IS NOT NULL AND fr.fwd_5d IS NOT NULL
        """ % (factor, factor), (dt,))
        rows = cur.fetchall()
        if len(rows) < 50:
            continue
        fv = np.array([float(r[0]) for r in rows if r[0] is not None and r[1] is not None])
        fw = np.array([float(r[1]) for r in rows if r[0] is not None and r[1] is not None])
        if len(fv) >= 50:
            ic = spearman_r(fv, fw)
            ics.append(ic)
    
    if ics:
        ic_mean = np.mean(ics)
        ic_std = np.std(ics, ddof=1) if len(ics) > 1 else 0
        ic_ir = ic_mean / ic_std if ic_std > 0 else 0
        n = len(ics)
        
        if abs(ic_mean) >= 0.05:
            tag = '🔥 强'
        elif abs(ic_mean) >= 0.03:
            tag = '中'
        elif abs(ic_mean) >= 0.015:
            tag = '弱'
        else:
            tag = '无效'
        
        direction = '趋势' if ic_mean > 0 else '反转'
        results.append((factor, ic_mean, ic_std, ic_ir, n, tag, direction))
    else:
        results.append((factor, 0, 0, 0, 0, '无数据', '-'))

# Sort by |IC|
results.sort(key=lambda x: abs(x[1]), reverse=True)

print("\n%-20s %8s %8s %8s %6s %s" % ('Factor', 'IC_Mean', 'IC_Std', 'IC_IR', 'N', 'Conclusion'))
print("-" * 70)
for name, ic, std, ir, n, tag, direction in results:
    print("%-20s %8.4f %8.4f %8.3f %6d  %s (%s)" % (name, ic, std, ir, n, tag, direction))

conn.close()
print("\nDone.")
