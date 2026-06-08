"""
Multi-factor synthesis v2: correlation + composite score + backtest.
Memory-safe: processes one date at a time.
"""
import psycopg2, numpy as np, sys
from datetime import date, timedelta

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

# Load close prices — streaming, per stock
print("Loading close prices (streaming)...")
cur.execute("SELECT ts_code, trade_date, close FROM daily WHERE trade_date >= '2024-06-01' AND close IS NOT NULL ORDER BY ts_code, trade_date")

# Build compact storage: {date: {code: close}}
from collections import defaultdict
close_by_date = defaultdict(dict)
count = 0
for code, dt, close in cur:
    close_by_date[dt][code] = float(close)
    count += 1
print("  %d rows, %d dates" % (count, len(close_by_date)))

# Get dates where we have factor data
print("Finding dates with factor coverage...")
dates_sorted = sorted(close_by_date.keys())

# Test a few dates for factor coverage
test_dates = []
for dt in dates_sorted[::20]:
    if len(test_dates) >= 5:
        break
    cols = ', '.join(['"%s"' % f for f in factor_names])
    cur.execute("SELECT COUNT(*) FROM factor_technical_wide WHERE trade_date=%s AND st_rev_20d IS NOT NULL AND ln_cap IS NOT NULL", (dt,))
    n = cur.fetchone()[0]
    if n >= 200:
        test_dates.append(dt)
        print("  %s: %d stocks" % (dt, n))

if not test_dates:
    # Try without ln_cap filter
    for dt in dates_sorted[::20]:
        cur.execute("SELECT COUNT(*) FROM factor_technical_wide WHERE trade_date=%s AND st_rev_20d IS NOT NULL", (dt,))
        n = cur.fetchone()[0]
        if n >= 200:
            test_dates.append(dt)
            print("  %s: %d stocks" % (dt, n))
        if len(test_dates) >= 5:
            break

if not test_dates:
    print("NO DATA FOUND! Checking raw table...")
    cur.execute("SELECT COUNT(*), MAX(trade_date) FROM factor_technical_wide")
    c, md = cur.fetchone()
    print("  Total rows: %s, max date: %s" % (c, md))
    sys.exit(1)

print("Using %d dates for correlation" % len(test_dates))

# ═══ Step 1: Correlation matrix ═══ 
print("\n=== Correlation Matrix ===")
all_vals = {f: [] for f in factor_names}

for dt in test_dates:
    cols = ', '.join(['"%s"::float' % f for f in factor_names])
    cur.execute("SELECT %s FROM factor_technical_wide WHERE trade_date=%%s AND st_rev_20d IS NOT NULL" % cols, (dt,))
    rows = cur.fetchall()
    for i, f in enumerate(factor_names):
        vals = [float(r[i]) for r in rows if r[i] is not None]
        all_vals[f].extend(vals[:500])

corr = np.zeros((n_factors, n_factors))
for i in range(n_factors):
    for j in range(i, n_factors):
        vi = np.array(all_vals[factor_names[i]])
        vj = np.array(all_vals[factor_names[j]])
        n = min(len(vi), len(vj))
        if n < 30:
            corr[i,j] = corr[j,i] = 0
            continue
        ri = np.argsort(np.argsort(vi[:n])).astype(float) + 1
        rj = np.argsort(np.argsort(vj[:n])).astype(float) + 1
        c = 1 - 6 * np.sum((ri - rj)**2) / (n * (n*n - 1))
        corr[i,j] = corr[j,i] = c

# Print
print("%-20s" % '', end='')
for f in factor_names:
    print("%6s" % f[:6], end='')
print()
for i, f in enumerate(factor_names):
    print("%-20s" % f, end='')
    for j in range(n_factors):
        v = corr[i,j]
        if i == j:
            print("%6s" % '1.0', end='')
        elif abs(v) > 0.5:
            print("\033[91m%6.2f\033[0m" % v, end='')
        else:
            print("%6.2f" % v, end='')
    print()

# ═══ Step 2: Composite score backtest ═══
print("\n=== Backtest: Top 20% equally-weighted ===")
TOP_PCT = 0.20
results = []

for dt in test_dates:
    # Check close data
    if dt not in close_by_date:
        continue
    
    closes_today = close_by_date[dt]
    
    cols = ', '.join(['ts_code'] + ['"%s"::float' % f for f in factor_names])
    cur.execute("SELECT %s FROM factor_technical_wide WHERE trade_date=%%s AND st_rev_20d IS NOT NULL" % cols, (dt,))
    rows = cur.fetchall()
    if len(rows) < 100:
        continue
    
    codes = [r[0] for r in rows]
    n = len(rows)
    
    # Build factor matrix
    factor_mat = np.zeros((n, n_factors))
    for i in range(n_factors):
        vals = np.array([float(r[i+1]) if r[i+1] is not None else np.nan for r in rows])
        mask = ~np.isnan(vals)
        if mask.sum() < 10:
            continue
        mu = np.mean(vals[mask])
        sigma = np.std(vals[mask])
        if sigma > 0:
            vals[mask] = (vals[mask] - mu) / sigma
        vals[~mask] = 0
        factor_mat[:, i] = vals
    
    composite = factor_mat @ signs / n_factors
    
    # Top 20%
    cutoff = int(n * (1 - TOP_PCT))
    top_idx = np.argsort(composite)[cutoff:]
    top_codes = [codes[i] for i in top_idx]
    
    # Forward 5-day return
    rets = []
    for code in top_codes:
        if code not in closes_today:
            continue
        # Find 5-day ahead close
        future_dates = [d for d in dates_sorted if d > dt]
        if len(future_dates) < 5:
            continue
        fwd_date = future_dates[4]  # 5th trading day
        if fwd_date not in close_by_date or code not in close_by_date[fwd_date]:
            continue
        ret = (close_by_date[fwd_date][code] / closes_today[code] - 1) * 100
        rets.append(ret)
    
    if len(rets) >= 10:
        avg_ret = np.mean(rets)
        results.append((dt, avg_ret))

if not results:
    print("No valid backtest results!")
    sys.exit(1)

# ═══ Performance stats ═══
rets = np.array([r[1] for r in results])
cum = np.cumprod(1 + rets / 100)
total_ret = (cum[-1] - 1) * 100
ann_ret = total_ret / len(results) * 250
vol = np.std(rets, ddof=1) * np.sqrt(250 / 5)
sharpe = ann_ret / vol if vol > 0 else 0
peak = np.maximum.accumulate(cum)
dd = (cum - peak) / peak * 100
max_dd = np.min(dd)
hit_rate = np.sum(rets > 0) / len(rets) * 100

print("\n" + "="*50)
print("MULTI-FACTOR BACKTEST")
print("="*50)
print("Period: %s → %s" % (results[0][0], results[-1][0]))
print("Dates: %d" % len(results))
print("Total return: %.1f%%" % total_ret)
print("Ann. return: %.1f%%" % ann_ret)
print("Ann. volatility: %.1f%%" % vol)
print("Sharpe: %.2f" % sharpe)
print("Max drawdown: %.1f%%" % max_dd)
print("Hit rate: %.1f%%" % hit_rate)

conn.close()
