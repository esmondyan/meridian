"""
Stratified backtest for strategy factors using pre-built fwd_returns table.
Memory-safe: uses server-side cursor for factor data, JOINs with fwd_returns.
"""
import sys, os
sys.path.insert(0, '/home/hermes/projects/stocksage/alpha')
with open('/home/hermes/projects/stocksage/alpha/.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

import numpy as np
import psycopg2
from datetime import date

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')
cur = conn.cursor()

# Config
HORIZONS = [5, 10, 20]
N_QUANTILES = 5
MIN_STOCKS = 100
FACTORS = [
    'yang_today', 'yin_streak', 'yang_after_yin_streak',
    'ma10_ma54_platform', 'limit_up_flag',
]

# Check data availability
cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM fwd_returns")
fd1, fd2 = cur.fetchone()
print(f"fwd_returns coverage: {fd1} ~ {fd2}")

# For each factor, load data and stratify
all_results = {}

def stats(arr):
    arr = np.array(arr)
    if len(arr) < 3:
        return (0, 0, 0, 0, 0, 0, 0)
    mean_r = np.mean(arr)
    vol = np.std(arr, ddof=1)
    sharpe = mean_r / vol if vol > 0 else 0
    hit_rate = np.sum(arr > 0) / len(arr) * 100
    cum = np.cumprod(1 + arr / 100)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100
    max_dd = np.min(dd)
    cum_ret = (cum[-1] - 1) * 100
    return (mean_r, vol, sharpe, max_dd, hit_rate, cum_ret, len(arr))

for factor in FACTORS:
    print(f"\n{'='*70}")
    print(f"Factor: {factor}")
    print(f"{'='*70}")
    
    # Load factor + forward returns via JOIN, grouped by date
    cur.execute(f"""
        SELECT f.trade_date, f.ts_code, f."{factor}"::float,
               fr.fwd_5d::float, fr.fwd_10d::float, fr.fwd_20d::float
        FROM factor_technical_wide f
        JOIN fwd_returns fr ON f.ts_code = fr.ts_code AND f.trade_date = fr.trade_date
        WHERE f."{factor}" IS NOT NULL
          AND f.trade_date >= '2025-01-01'
        ORDER BY f.trade_date
    """)
    
    # Process by date
    results = {h: {q: [] for q in range(N_QUANTILES)} for h in HORIZONS}
    date_data = {}  # {date: [(val, fwd5, fwd10, fwd20), ...]}
    
    for dt, code, fv, f5, f10, f20 in cur:
        if fv is None or f5 is None or f10 is None or f20 is None:
            continue
        date_data.setdefault(dt, []).append((fv, f5, f10, f20))
    
    n_dates = 0
    for dt in sorted(date_data.keys()):
        rows = date_data[dt]
        if len(rows) < MIN_STOCKS:
            continue
        
        vals = np.array([r[0] for r in rows])
        fwd_arr = np.array([[r[1], r[2], r[3]] for r in rows])
        
        q_bounds = np.percentile(vals, np.linspace(0, 100, N_QUANTILES + 1))
        q_bounds[0] = -np.inf
        q_bounds[-1] = np.inf
        
        for q in range(N_QUANTILES):
            mask = (vals > q_bounds[q]) & (vals <= q_bounds[q + 1])
            if mask.sum() < 10:
                continue
            for hi, h in enumerate(HORIZONS):
                results[h][q].append(float(np.mean(fwd_arr[mask, hi])))
        
        n_dates += 1
    
    print(f"  {n_dates} dates processed")
    
    # Report per-horizon
    factor_result = {}
    for h in HORIZONS:
        print(f"\n  Forward {h}-day:")
        print(f"  {'Quintile':<12} {'N':>6} {'Mean%':>8} {'Std%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'Hit%':>7} {'Cum%':>8}")
        print(f"  {'':-<12} {'':->6} {'':->8} {'':->8} {'':->8} {'':->8} {'':->7} {'':->8}")
        
        q_means = []
        for q in range(N_QUANTILES):
            m, s, sh, dd, hr, cr, n = stats(results[h][q])
            q_means.append(m)
            print(f"  Q{q:<11} {n:>6} {m:>8.3f} {s:>8.3f} {sh:>8.3f} {dd:>8.1f} {hr:>7.1f} {cr:>8.1f}")
        
        spread = q_means[0] - q_means[-1]
        print(f"  {'':-<12} {'':->6} {'':->8} {'':->8} {'':->8} {'':->8} {'':->7} {'':->8}")
        print(f"  {'Spread(Q0-Q4)':<12} {'':>6} {spread:>8.3f}")
        
        factor_result[h] = {'quintile_means': q_means, 'spread': spread}
    
    all_results[factor] = factor_result

# Summary
print(f"\n{'='*80}")
print("CROSS-FACTOR SUMMARY (5-day, Q0-Q4 spread)")
print(f"{'='*80}")
print(f"{'Factor':<28} {'Spread%':>8} {'Direction':<20}")
print("-" * 56)
for factor in FACTORS:
    r = all_results.get(factor, {}).get(5, {})
    spread = r.get('spread', 0)
    direction = "Q0 wins (low→high)" if spread > 0 else "Q4 wins (high→high)"
    print(f"{factor:<28} {spread:>8.3f}  {direction}")

conn.close()
print("\nDone.")
