"""
Stratified backtest using SQL window functions — no large in-memory dicts.
Computes forward returns via LEAD() in Postgres, then streams date-by-date.
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

HORIZONS = [5, 10, 20]
N_QUANTILES = 5
MIN_STOCKS = 100

# ── Step 1: Create temp table with forward returns ──
print("Creating temp table with forward returns (LEAD window)...")
h_cols = ", ".join(f"""
    (LEAD(close, {h}) OVER w / close - 1) * 100 AS fwd_{h}d
""" for h in HORIZONS)

cur.execute(f"""
    DROP TABLE IF EXISTS tmp_fwd_returns;
    CREATE TEMP TABLE tmp_fwd_returns AS
    SELECT 
        ts_code,
        trade_date,
        {h_cols}
    FROM daily
    WHERE trade_date >= '2023-12-01'
    WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date);
    
    CREATE INDEX ON tmp_fwd_returns(trade_date);
    CREATE INDEX ON tmp_fwd_returns(ts_code, trade_date);
""")
conn.commit()

cur.execute("SELECT COUNT(*) FROM tmp_fwd_returns")
n_fwd = cur.fetchone()[0]
print(f"  {n_fwd} rows")

# ── Step 2: Get distinct dates with price_position ──
print("Getting dates from factor table...")
cur.execute("""
    SELECT DISTINCT trade_date FROM factor_technical_wide 
    WHERE price_position IS NOT NULL 
    ORDER BY trade_date
""")
dates = [row[0] for row in cur]
print(f"  {len(dates)} dates")

# ── Step 3: For each date, load joined data, stratify, track returns ──
print("Running stratified backtest (date by date)...")

# results[h][q] = list of period returns
results = {h: {q: [] for q in range(N_QUANTILES)} for h in HORIZONS}
fwd_cols = [f"f.fwd_{h}d" for h in HORIZONS]

for i, dt in enumerate(dates):
    cols = ", ".join(fwd_cols)
    cur.execute(f"""
        SELECT fw.pp, {", ".join(f"fw.fwd_{h}d" for h in HORIZONS)}
        FROM (
            SELECT f.ts_code, f.price_position AS pp, {", ".join(f"fwd.fwd_{h}d" for h in HORIZONS)}
            FROM factor_technical_wide f
            JOIN tmp_fwd_returns fwd ON f.ts_code = fwd.ts_code AND f.trade_date = fwd.trade_date
            WHERE f.trade_date = %s AND f.price_position IS NOT NULL
        ) fw
        WHERE {" AND ".join(f"fw.fwd_{h}d IS NOT NULL" for h in HORIZONS)}
    """, (dt,))
    rows = cur.fetchall()
    
    if len(rows) < MIN_STOCKS:
        continue
    
    pp_values = np.array([float(r[0]) for r in rows])
    
    # Assign quantiles
    q_bounds = np.percentile(pp_values, np.linspace(0, 100, N_QUANTILES + 1))
    q_bounds[0] = -np.inf
    q_bounds[-1] = np.inf
    
    for q in range(N_QUANTILES):
        mask = (pp_values > q_bounds[q]) & (pp_values <= q_bounds[q+1])
        if mask.sum() < 10:
            continue
        for hi, h in enumerate(HORIZONS):
            q_ret = np.mean([float(rows[j][hi+1]) for j, m in enumerate(mask) if m and rows[j][hi+1] is not None])
            if not np.isnan(q_ret):
                results[h][q].append(q_ret)
    
    if (i + 1) % 100 == 0:
        print(f"  {i+1}/{len(dates)} dates done")

print(f"  all {len(dates)} dates done")

# ── Step 4: Statistics ──
def calc_stats(returns):
    arr = np.array(returns)
    if len(arr) == 0:
        return (0, 0, 0, 0, 0, 0)
    mean_r = np.mean(arr)
    vol = np.std(arr, ddof=1)
    sharpe = mean_r / vol if vol > 0 else 0
    hit_rate = np.sum(arr > 0) / len(arr) * 100
    cum = np.cumprod(1 + arr / 100)
    peak = np.maximum.accumulate(cum)
    max_dd = np.min((cum - peak) / peak * 100)
    return (mean_r, vol, sharpe, max_dd, hit_rate)

print("\n" + "="*80)
print("PRICE_POSITION STRATIFIED BACKTEST")
print("="*80)
print("Q0 = lowest price_position (close near day's low)")
print(f"Q{N_QUANTILES-1} = highest price_position (close near day's high)")
print()

for h in HORIZONS:
    print(f"\n{'─'*60}")
    print(f"Forward {h}-day returns  │  {len(results[h][0])} dates")
    print(f"{'─'*60}")
    print(f"{'Quintile':<12} {'Mean%':>8} {'Std%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'Hit%':>8}")
    print(f"{'':-<12} {'':->8} {'':->8} {'':->8} {'':->8} {'':->8}")
    
    q_means = []
    for q in range(N_QUANTILES):
        m, s, sh, dd, hr = calc_stats(results[h][q])
        q_means.append(m)
        print(f"Q{q:<11} {m:>8.3f} {s:>8.3f} {sh:>8.3f} {dd:>8.1f} {hr:>7.1f}")
    
    spread = q_means[0] - q_means[-1]
    print(f"{'':-<12} {'':->8} {'':->8} {'':->8} {'':->8} {'':->8}")
    print(f"{'Spread':<12} {spread:>8.3f}")

# ── Long-Short ──
print(f"\n{'='*60}")
print("LONG-SHORT (Q0 − Q4)")
print(f"{'='*60}")
for h in HORIZONS:
    n = min(len(results[h][0]), len(results[h][-1]))
    ls_rets = [results[h][0][i] - results[h][-1][i] for i in range(n)]
    m, s, sh, dd, hr = calc_stats(ls_rets)
    print(f"{h}d: mean={m:.3f}%  vol={s:.3f}%  sharpe={sh:.3f}  maxDD={dd:.1f}%  hit={hr:.1f}%  n={n}")

cur.execute("DROP TABLE IF EXISTS tmp_fwd_returns")
conn.commit()
conn.close()
print("\nDone.")
