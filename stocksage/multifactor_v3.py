"""
Multi-factor synthesis v3: zero-memory backtest.
- One date at a time
- Forward returns from SQL (fwd_returns JOIN)
- Only stores return history in memory (~500 floats)
"""
import psycopg2, numpy as np, sys

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password='hermes_quant_2026', dbname='stocksage_alpha')
cur = conn.cursor()

ALL_FACTORS = [
    ('st_rev_20d', -1), ('max_ret_20d', -1), ('ivol_20d', -1),
    ('mom_12m_1m', 1), ('amihud_illiq', 1), ('ln_cap', -1),
    ('pb', -1), ('pe_ttm', -1), ('roe', 1), ('roa', 1),
    ('consecutive_down', 1), ('volatility_20d', -1), ('williams_r', -1),
]
n_factors = len(ALL_FACTORS)
factor_names = [f[0] for f in ALL_FACTORS]
signs = np.array([f[1] for f in ALL_FACTORS])

print("Factors: %d" % n_factors)

# Get available dates (fast, with index)
print("Finding dates...")
cur.execute("""
    SELECT trade_date, COUNT(*) FROM factor_technical_wide
    WHERE st_rev_20d IS NOT NULL AND trade_date >= '2025-01-01'
    GROUP BY trade_date HAVING COUNT(*) >= 300
    ORDER BY trade_date
""")
dates_with_counts = cur.fetchall()
if not dates_with_counts:
    cur.execute("""
        SELECT trade_date, COUNT(*) FROM factor_technical_wide
        WHERE st_rev_20d IS NOT NULL AND trade_date >= '2025-01-01'
        GROUP BY trade_date HAVING COUNT(*) >= 100
        ORDER BY trade_date
    """)
    dates_with_counts = cur.fetchall()

DATES = [d[0] for d in dates_with_counts[:200]]  # last ~200 trading days
print("  %d dates" % len(DATES))

if not DATES:
    print("No dates! Checking table...")
    cur.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM factor_technical_wide WHERE st_rev_20d IS NOT NULL")
    print("  %s" % str(cur.fetchone()))
    sys.exit(1)

# ═══ Step 1: Correlation (sample ~20 dates) ═══
print("\n=== Correlation ===")
sample_dates = DATES[::max(1, len(DATES)//20)][:20]
all_vals = {f: [] for f in factor_names}

for dt in sample_dates:
    cols = ', '.join(['"%s"::float' % f for f in factor_names])
    cur.execute("SELECT %s FROM factor_technical_wide WHERE trade_date=%%s" % cols, (dt,))
    rows = cur.fetchall()
    for i, f in enumerate(factor_names):
        vals = [float(r[i]) for r in rows if r[i] is not None]
        all_vals[f].extend(vals[:300])

corr = np.zeros((n_factors, n_factors))
for i in range(n_factors):
    for j in range(i, n_factors):
        vi = np.array(all_vals[factor_names[i]])
        vj = np.array(all_vals[factor_names[j]])
        n = min(len(vi), len(vj))
        if n < 30: continue
        ri = np.argsort(np.argsort(vi[:n])).astype(float) + 1
        rj = np.argsort(np.argsort(vj[:n])).astype(float) + 1
        c = 1 - 6 * np.sum((ri - rj)**2) / (n * (n*n - 1))
        corr[i,j] = corr[j,i] = c

# Print correlation
print("%-20s" % '', end='')
for f in factor_names:
    print("%6s" % f[:6], end='')
print()
for i, f in enumerate(factor_names):
    print("%-20s" % f, end='')
    for j in range(n_factors):
        v = corr[i,j]
        if i == j: print("%6s" % '1.0', end='')
        elif abs(v) > 0.5: print("\033[91m%6.2f\033[0m" % v, end='')
        else: print("%6.2f" % v, end='')
    print()

# ═══ Step 2: Backtest (one date at a time) ═══
print("\n=== Backtest: Top 20% ===")
TOP_PCT = 0.20
results = []

# Pre-build the factor column SQL
factor_sql = ', '.join(['ts_code'] + ['"%s"::float' % f for f in factor_names])

for di, dt in enumerate(DATES):
    # Load factor values for this date
    cur.execute("SELECT %s FROM factor_technical_wide WHERE trade_date=%%s" % factor_sql, (dt,))
    rows = cur.fetchall()
    if len(rows) < 100: continue
    
    codes = [r[0] for r in rows]
    n = len(rows)
    
    # Build factor matrix
    factor_mat = np.zeros((n, n_factors))
    for i in range(n_factors):
        vals = np.array([float(r[i+1]) if r[i+1] is not None else np.nan for r in rows])
        mask = ~np.isnan(vals)
        if mask.sum() < 10: continue
        mu = np.mean(vals[mask])
        sigma = np.std(vals[mask])
        if sigma > 0:
            vals[mask] = (vals[mask] - mu) / sigma
        vals[~mask] = 0
        factor_mat[:, i] = vals
    
    # Composite score
    composite = factor_mat @ signs / n_factors
    
    # Top 20%
    cutoff = int(n * (1 - TOP_PCT))
    top_idx = np.argsort(composite)[cutoff:]
    top_codes = [codes[i] for i in top_idx]
    
    # Get forward 5-day returns via SQL JOIN with fwd_returns
    code_list = ','.join("'%s'" % c for c in top_codes[:500])  # cap at 500
    cur.execute("""
        SELECT AVG(fr.fwd_5d) FROM fwd_returns fr
        WHERE fr.ts_code IN (%s) AND fr.trade_date = %%s
        AND fr.fwd_5d IS NOT NULL
    """ % code_list, (dt,))
    avg_ret = cur.fetchone()[0]
    
    if avg_ret is not None:
        results.append((dt, avg_ret))
    
    if (di+1) % 30 == 0:
        cum = np.prod(1 + np.array([r[1] for r in results]) / 100)
        print("  %s: %d/%d, cum=%.1f%%" % (dt, di+1, len(DATES), (cum-1)*100))

if len(results) < 10:
    print("Not enough results (%d)" % len(results))
    sys.exit(1)

# ═══ Stats ═══
rets = np.array([r[1] for r in results])
cum = np.cumprod(1 + rets / 100)
total_ret = (cum[-1] - 1) * 100
ann_ret = total_ret / len(results) * 250
vol = np.std(rets, ddof=1) * np.sqrt(250 / 5)
sharpe = ann_ret / vol if vol > 0 else 0
peak = np.maximum.accumulate(cum)
dd_pct = np.min((cum - peak) / peak * 100)
hit = np.sum(rets > 0) / len(rets) * 100

print("\n" + "="*50)
print("MULTI-FACTOR BACKTEST RESULTS")
print("="*50)
print("Period: %s → %s" % (results[0][0], results[-1][0]))
print("N dates: %d" % len(results))
print("Total return: %.1f%%" % total_ret)
print("Ann. return: %.1f%%" % ann_ret)
print("Ann. volatility: %.1f%%" % vol)
print("Sharpe: %.2f" % sharpe)
print("Max drawdown: %.1f%%" % dd_pct)
print("Hit rate: %.1f%%" % hit)

conn.close()
