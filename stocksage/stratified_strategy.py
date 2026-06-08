"""
Stratified backtest for strategy-derived factors.
For each factor:
  1. Each day, split stocks into 5 quintiles by factor value
  2. Track equal-weighted forward returns (5d, 10d, 20d) per quintile
  3. Report mean return, Sharpe, max drawdown, hit rate, cumulative curves
  4. Long-short: Q0 - Q4 spread

Uses on-the-fly forward return computation from daily.close
(because fwd_returns table only goes to 2026-05-08).
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
from datetime import date, timedelta

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')
cur = conn.cursor()

# ── Config ──
HORIZONS = [5, 10, 20]
N_QUANTILES = 5
MIN_STOCKS = 100

# Analysis dates: use the same 3 dates as IC analysis, plus extend to match IC pattern
# Actually, for stratified backtest we want MORE dates for statistical power.
# Use all dates where strategy factors exist AND we can compute forward returns.
START_DATE = '2025-01-01'  # One year of data
END_DATE = '2026-06-05'     # Last daily data

FACTORS_TO_TEST = [
    'yang_today',
    'yin_streak',
    'yang_after_yin_streak',
    'ma10_ma54_platform',
    'limit_up_flag',
]

print("=" * 80)
print("STRATEGY FACTOR STRATIFIED BACKTEST")
print(f"Date range: {START_DATE} ~ {END_DATE}")
print(f"Horizons: {HORIZONS}")
print(f"Factors: {FACTORS_TO_TEST}")
print("=" * 80)

# ── Step 1: Load close prices ──
print("\n[1/4] Loading close prices...")
cur.execute("""
    SELECT ts_code, trade_date, close FROM daily
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY ts_code, trade_date
""", (START_DATE, END_DATE))

close_map = {}
count = 0
for code, dt, close in cur:
    if close is None:
        continue
    close_map.setdefault(code, {})[dt] = float(close)
    count += 1
print(f"  {len(close_map):,} stocks, {count:,} close rows")

# ── Step 2: Compute forward returns (on-the-fly, since fwd_returns is stale) ──
print("[2/4] Computing forward returns...")
fwd_returns = {h: {} for h in HORIZONS}

for code, d in close_map.items():
    dates = sorted(d.keys())
    for i, dt in enumerate(dates):
        for h in HORIZONS:
            if i + h < len(dates):
                ret = (d[dates[i + h]] / d[dt] - 1) * 100
                fwd_returns[h].setdefault(code, {})[dt] = ret

del close_map

for h in HORIZONS:
    n = sum(len(v) for v in fwd_returns[h].values())
    print(f"  {h}d: {n:,} observations")

# ── Step 3: Load factor data ──
print("[3/4] Loading factor data...")

all_results = {}

for factor in FACTORS_TO_TEST:
    print(f"\n{'─' * 70}")
    print(f"  Factor: {factor}")
    
    # Load factor values
    cur.execute(f"""
        SELECT ts_code, trade_date, "{factor}" FROM factor_technical_wide
        WHERE "{factor}" IS NOT NULL AND trade_date >= %s AND trade_date <= %s
        ORDER BY trade_date
    """, (START_DATE, END_DATE))
    
    factor_data = {}  # factor_data[date][code] = value
    for code, dt, fval in cur:
        factor_data.setdefault(dt, {})[code] = float(fval)
    
    print(f"    {len(factor_data)} dates with data")
    
    # ── Step 4: Stratify ──
    results = {h: {q: [] for q in range(N_QUANTILES)} for h in HORIZONS}
    date_count = 0
    
    for dt in sorted(factor_data.keys()):
        fv_today = factor_data[dt]
        
        # Filter to stocks with forward returns at all horizons
        valid_codes = []
        valid_vals = []
        for code, fv in fv_today.items():
            has_all = all(
                code in fwd_returns[h] and dt in fwd_returns[h][code]
                for h in HORIZONS
            )
            if has_all:
                valid_codes.append(code)
                valid_vals.append(fv)
        
        if len(valid_codes) < MIN_STOCKS:
            continue
        
        fv_arr = np.array(valid_vals)
        
        # Assign to quintiles
        # For factors where HIGHER is better (positive IC): Q4 = highest
        # For factors where LOWER is better (negative IC): Q0 = lowest  
        # We'll always sort ascending: Q0=lowest values, Q4=highest values
        q_bounds = np.percentile(fv_arr, np.linspace(0, 100, N_QUANTILES + 1))
        q_bounds[0] = -np.inf
        q_bounds[-1] = np.inf
        
        for q in range(N_QUANTILES):
            mask = (fv_arr > q_bounds[q]) & (fv_arr <= q_bounds[q + 1])
            q_codes = [valid_codes[i] for i, m in enumerate(mask) if m]
            if len(q_codes) < 10:
                continue
            
            for h in HORIZONS:
                q_ret = np.mean([
                    fwd_returns[h][c][dt] for c in q_codes
                    if dt in fwd_returns[h][c]
                ])
                results[h][q].append(q_ret)
        
        date_count += 1
    
    print(f"    {date_count} dates processed (after MIN_STOCKS={MIN_STOCKS} filter)")
    
    # ── Stats ──
    def compute_stats(returns):
        arr = np.array(returns)
        if len(arr) == 0:
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
    
    factor_result = {}
    
    for h in HORIZONS:
        print(f"\n    Forward {h}-day returns:")
        print(f"    {'Quintile':<12} {'N_dates':>8} {'Mean%':>8} {'Std%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'Hit%':>7} {'CumRet%':>9}")
        print(f"    {'':-<12} {'':->8} {'':->8} {'':->8} {'':->8} {'':->8} {'':->7} {'':->9}")
        
        q_means = []
        for q in range(N_QUANTILES):
            m, s, sh, dd, hr, cr, n = compute_stats(results[h][q])
            q_means.append(m)
            print(f"    Q{q:<11} {n:>8} {m:>8.3f} {s:>8.3f} {sh:>8.3f} {dd:>8.1f} {hr:>7.1f} {cr:>9.1f}")
        
        # Spread
        spread = q_means[0] - q_means[-1]
        print(f"    {'':-<12} {'':->8} {'':->8} {'':->8} {'':->8} {'':->8} {'':->7} {'':->9}")
        print(f"    {'Spread(Q0-Q4)':<12} {'':>8} {spread:>8.3f}")
        
        # Long-short stats
        ls_rets = []
        n_ls = min(len(results[h][0]), len(results[h][-1]))
        for i in range(n_ls):
            ls_rets.append(results[h][0][i] - results[h][-1][i])
        m, s, sh, dd, hr, cr, _ = compute_stats(ls_rets)
        print(f"    {'LS(Q0-Q4)':<12} {n_ls:>8} {m:>8.3f} {s:>8.3f} {sh:>8.3f} {dd:>8.1f} {hr:>7.1f} {cr:>9.1f}")
        
        factor_result[h] = {
            'quintile_means': q_means,
            'spread': spread,
            'ls_sharpe': sh,
            'ls_cumret': cr,
        }
    
    all_results[factor] = factor_result

# ── Summary ──
print("\n" + "=" * 80)
print("CROSS-FACTOR SUMMARY (5-day forward, Q0-Q4 spread)")
print("=" * 80)
print(f"{'Factor':<28} {'Spread%':>8} {'LS_Sharpe':>9} {'LS_CumRet%':>10} {'Direction'}")
print("-" * 70)

for factor in FACTORS_TO_TEST:
    if factor in all_results and 5 in all_results[factor]:
        r = all_results[factor][5]
        spread = r['spread']
        direction = "Q0 wins (low→high return)" if spread > 0 else "Q4 wins (high→high return)"
        print(f"{factor:<28} {spread:>8.3f} {r['ls_sharpe']:>9.3f} {r['ls_cumret']:>10.1f}  {direction}")

print("\nDone.")
conn.close()
