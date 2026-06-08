"""Run stratification on existing fwd_returns table (5511 stocks, 3.1M rows)."""
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

HORIZONS = [5, 10, 20]
N_QUANTILES = 5
MIN_STOCKS = 100

# ── Get dates from the smaller fwd_returns table ──
print("Getting dates...")
cur.execute("SELECT DISTINCT trade_date FROM fwd_returns ORDER BY trade_date")
dates = [row[0] for row in cur]
print(f"  {len(dates)} dates: {dates[0]} to {dates[-1]}")

# ── Stream date by date ──
print("Running stratification...")
results = {h: {q: [] for q in range(N_QUANTILES)} for h in HORIZONS}
n_stocks_per_date = []

for i, dt in enumerate(dates):
    cur.execute("""
        SELECT f.price_position, fr.fwd_5d, fr.fwd_10d, fr.fwd_20d
        FROM factor_technical_wide f
        JOIN fwd_returns fr ON f.ts_code = fr.ts_code AND f.trade_date = fr.trade_date
        WHERE f.trade_date = %s AND f.price_position IS NOT NULL
    """, (dt,))
    rows = cur.fetchall()
    n_stocks_per_date.append(len(rows))
    
    if len(rows) < MIN_STOCKS:
        continue
    
    pp_values = np.array([float(r[0]) for r in rows])
    fwd_arr = np.array([[float(r[1]), float(r[2]), float(r[3])] for r in rows])
    
    q_bounds = np.percentile(pp_values, np.linspace(0, 100, N_QUANTILES + 1))
    q_bounds[0] = -np.inf
    q_bounds[-1] = np.inf
    
    for q in range(N_QUANTILES):
        mask = (pp_values > q_bounds[q]) & (pp_values <= q_bounds[q+1])
        if mask.sum() < 10:
            continue
        for hi, h in enumerate(HORIZONS):
            q_ret = float(np.mean(fwd_arr[mask, hi]))
            results[h][q].append(q_ret)
    
    if (i + 1) % 100 == 0:
        print(f"  {i+1}/{len(dates)}")

print(f"  done: {len(dates)} dates, avg {np.mean(n_stocks_per_date):.0f} stocks/date")

# ── Statistics ──
def calc_stats(returns):
    arr = np.array(returns)
    n = len(arr)
    if n == 0:
        return (0, 0, 0, 0, 0, 0, 0)
    mean_r = np.mean(arr)
    vol = np.std(arr, ddof=1)
    sharpe = mean_r / vol if vol > 0 else 0
    hit_rate = np.sum(arr > 0) / n * 100
    cum = np.cumprod(1 + arr / 100)
    peak = np.maximum.accumulate(cum)
    max_dd = np.min((cum - peak) / peak * 100)
    total_ret = (cum[-1] - 1) * 100
    return (n, mean_r, vol, sharpe, max_dd, hit_rate, total_ret)

print("\n" + "="*80)
print("PRICE_POSITION STRATIFIED BACKTEST")
print("="*80)
print(f"Period: {dates[0]} → {dates[-1]}  ({len(dates)} dates)")
print(f"Stocks per date: {np.mean(n_stocks_per_date):.0f} avg, {np.min(n_stocks_per_date)} min, {np.max(n_stocks_per_date)} max")
print(f"\nQ0 = lowest price_position (close near LOW → expected HIGH returns)")
print(f"Q4 = highest price_position (close near HIGH → expected LOW returns)")
print()

for h in HORIZONS:
    print(f"\n{'─'*65}")
    print(f"Forward {h}-day returns  │  {len(results[h][0])} trading dates")
    print(f"{'─'*65}")
    print(f"{'Quintile':<10} {'N':>5} {'Mean%':>8} {'Std%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'Hit%':>7} {'CumRet%':>9}")
    print(f"{'':-<10} {'':->5} {'':->8} {'':->8} {'':->8} {'':->8} {'':->7} {'':->9}")
    
    q_means = []
    for q in range(N_QUANTILES):
        n, m, s, sh, dd, hr, cr = calc_stats(results[h][q])
        q_means.append(m)
        print(f"Q{q:<9} {n:>5} {m:>8.3f} {s:>8.3f} {sh:>8.3f} {dd:>8.1f} {hr:>6.1f} {cr:>8.1f}")
    
    spread = q_means[0] - q_means[-1]
    print(f"{'':-<10} {'':->5} {'':->8} {'':->8} {'':->8} {'':->8} {'':->7} {'':->9}")
    print(f"{'Spread':<10} {'':>5} {spread:>8.3f}")

# ── Long-Short ──
print(f"\n{'='*60}")
print("LONG-SHORT PORTFOLIO (Q0 − Q4)")
print(f"{'='*60}")
for h in HORIZONS:
    n = min(len(results[h][0]), len(results[h][-1]))
    ls_rets = [results[h][0][i] - results[h][-1][i] for i in range(n)]
    n2, m, s, sh, dd, hr, cr = calc_stats(ls_rets)
    print(f"{h}d: mean={m:.3f}%  vol={s:.3f}%  sharpe={sh:.3f}  maxDD={dd:.1f}%  hit={hr:.1f}%  cum={cr:.1f}%")

# ── Also show cumulative chart data (text) ──
print(f"\n{'='*60}")
print("CUMULATIVE RETURN CURVES (5-day forward)")
print(f"{'='*60}")
print("Date       Q0_cum     Q1_cum     Q2_cum     Q3_cum     Q4_cum     LS_cum")
print("─"*70)
# Sample every ~20 dates for readable output
step = max(1, len(dates) // 20)
q_cums = {q: np.cumprod(1 + np.array(results[5][q]) / 100) for q in range(N_QUANTILES)}
ls_cum = np.cumprod(1 + np.array([results[5][0][i] - results[5][-1][i] 
                                   for i in range(min(len(results[5][0]), len(results[5][-1])))] / 100))
for i in range(0, len(dates), step):
    dt = dates[i]
    vals = [q_cums[q][i] if i < len(q_cums[q]) else float('nan') for q in range(N_QUANTILES)]
    ls = ls_cum[i] if i < len(ls_cum) else float('nan')
    parts = [f"{dt}"] + [f"{v:.4f}" for v in vals] + [f"{ls:.4f}"]
    print("  ".join(parts))

conn.close()
print("\nDone.")
