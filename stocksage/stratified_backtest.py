"""
Stratified backtest: sort stocks by price_position into quintiles each day,
track forward returns per quintile.

price_position = (close - low) / (high - low) * 100
  0 = close at day's low, 100 = close at day's high

Expectation (from IC=-0.073): Lowest quintile (close near low) → highest fwd returns.
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

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')
cur = conn.cursor()

# ── Params ──
HORIZONS = [5, 10, 20]   # forward return horizons (trading days)
N_QUANTILES = 5          # Q0=lowest price_position, Q4=highest
MIN_STOCKS = 100         # skip dates with too few stocks

# ── Step 1: Load close prices ──
print("Loading close prices from daily...")
cur.execute("""
    SELECT ts_code, trade_date, close FROM daily
    WHERE trade_date >= '2023-12-01'
    ORDER BY ts_code, trade_date
""")
close_map = {}
count = 0
for code, dt, close in cur:
    if close is None: continue
    close_map.setdefault(code, {})[dt] = float(close)
    count += 1
print(f"  {len(close_map)} stocks, {count} close rows")

# ── Step 2: Compute forward returns ──
print("Computing forward returns...")
fwd_returns = {h: {} for h in HORIZONS}  # fwd_returns[h][code][date] = return%

for code, d in close_map.items():
    dates = sorted(d.keys())
    for i, dt in enumerate(dates):
        for h in HORIZONS:
            if i + h < len(dates):
                ret = (d[dates[i+h]] / d[dt] - 1) * 100
                fwd_returns[h].setdefault(code, {})[dt] = ret
del close_map
for h in HORIZONS:
    n = sum(len(v) for v in fwd_returns[h].values())
    print(f"  {h}d: {n} observations")

# ── Step 3: Load price_position for all dates ──
print("Loading price_position from factor_technical_wide...")
cur.execute("""
    SELECT ts_code, trade_date, price_position FROM factor_technical_wide
    WHERE price_position IS NOT NULL
    ORDER BY trade_date
""")
pp_data = {}  # pp_data[date][code] = price_position
for code, dt, pp in cur:
    pp_data.setdefault(dt, {})[code] = float(pp)
print(f"  {len(pp_data)} dates")

# ── Step 4: Stratify and track returns ──
print("Running stratified backtest...")
results = {h: {q: [] for q in range(N_QUANTILES)} for h in HORIZONS}
date_count = 0

for dt in sorted(pp_data.keys()):
    # Get price_position for this date
    pp_today = pp_data[dt]
    
    # Filter to stocks that have forward returns
    valid_stocks = {}
    for h in HORIZONS:
        for code in pp_today:
            if code in fwd_returns[h] and dt in fwd_returns[h][code]:
                valid_stocks.setdefault(code, set()).add(h)
    
    # Only keep stocks with all horizons
    valid_codes = sorted(c for c, hs in valid_stocks.items() if len(hs) == len(HORIZONS))
    if len(valid_codes) < MIN_STOCKS:
        continue
    
    # Get price_position values and sort
    pp_values = np.array([pp_today[c] for c in valid_codes])
    
    # Assign to quantiles (0 = lowest pp, N_QUANTILES-1 = highest pp)
    quantile_bounds = np.percentile(pp_values, np.linspace(0, 100, N_QUANTILES + 1))
    quantile_bounds[0] = -np.inf
    quantile_bounds[-1] = np.inf
    
    # For each quantile, compute equal-weighted forward return
    for q in range(N_QUANTILES):
        mask = (pp_values > quantile_bounds[q]) & (pp_values <= quantile_bounds[q+1])
        q_codes = [valid_codes[i] for i, m in enumerate(mask) if m]
        if len(q_codes) < 10:
            continue
        
        for h in HORIZONS:
            q_ret = np.mean([fwd_returns[h][c][dt] for c in q_codes if dt in fwd_returns[h][c]])
            results[h][q].append(q_ret)
    
    date_count += 1
    if date_count % 100 == 0:
        print(f"  processed {date_count} dates...")

print(f"  done: {date_count} dates processed")

# ── Step 5: Compute statistics ──
print("\n" + "="*80)
print("PRICE_POSITION STRATIFIED BACKTEST RESULTS")
print("="*80)
print(f"Quintile 0 (Q0) = lowest price_position (close near day's low)")
print(f"Quintile {N_QUANTILES-1} (Q{N_QUANTILES-1}) = highest price_position (close near day's high)")
print()

def stats(returns):
    """Returns: mean, annualized_return, annualized_vol, sharpe, max_dd, hit_rate"""
    arr = np.array(returns)
    if len(arr) == 0:
        return (0, 0, 0, 0, 0, 0)
    mean_r = np.mean(arr)
    vol = np.std(arr, ddof=1)
    n = len(arr)
    
    # Annualized: 250 trading days, but these are h-day returns
    # We'll report per-period stats, not annualized (clearer for stratified)
    sharpe = mean_r / vol if vol > 0 else 0
    hit_rate = np.sum(arr > 0) / n * 100
    
    cum = np.cumprod(1 + arr / 100)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100
    max_dd = np.min(dd)
    
    return (mean_r, vol, sharpe, max_dd, hit_rate)

for h in HORIZONS:
    print(f"\n{'─'*60}")
    print(f"Forward {h}-day returns")
    print(f"{'─'*60}")
    print(f"{'Quintile':<12} {'N_dates':>8} {'Mean%':>8} {'Std%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'Hit%':>8}")
    print(f"{'':-<12} {'':->8} {'':->8} {'':->8} {'':->8} {'':->8} {'':->8}")
    
    q_means = []
    for q in range(N_QUANTILES):
        rets = results[h][q]
        m, s, sh, dd, hr = stats(rets)
        q_means.append(m)
        n = len(rets)
        print(f"Q{q:<11} {n:>8} {m:>8.3f} {s:>8.3f} {sh:>8.3f} {dd:>8.1f} {hr:>7.1f}")
    
    # Spread: Q0 - Q4 (should be positive if price_position works)
    spread = q_means[0] - q_means[-1]
    print(f"{'':-<12} {'':->8} {'':->8} {'':->8} {'':->8} {'':->8} {'':->8}")
    print(f"{'Spread(Q0-Q4)':<12} {'':>8} {spread:>8.3f}")

# ── Step 6: Long-short portfolio (long Q0, short Q4) ──
print(f"\n{'='*60}")
print("LONG-SHORT (Q0 − Q4) portfolio stats")
print(f"{'='*60}")
for h in HORIZONS:
    ls_rets = []
    n = min(len(results[h][0]), len(results[h][-1]))
    for i in range(n):
        ls_rets.append(results[h][0][i] - results[h][-1][i])
    m, s, sh, dd, hr = stats(ls_rets)
    print(f"{h}d: mean={m:.3f}%  vol={s:.3f}%  sharpe={sh:.3f}  maxDD={dd:.1f}%  hit={hr:.1f}%  n={n}")

print("\nDone.")
