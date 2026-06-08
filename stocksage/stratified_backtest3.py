"""
Stratified backtest v3 — stream per-stock, build permanent fwd_returns table.
Memory: O(stock's history size), not O(all stocks).
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
import psycopg2.extras

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')
cur = conn.cursor()

HORIZONS = [5, 10, 20]
N_QUANTILES = 5
MIN_STOCKS = 100

# ── Step 1: Build permanent fwd_returns table (if not exists) ──
cur.execute("SELECT EXISTS(SELECT FROM pg_tables WHERE tablename = 'fwd_returns')")
exists = cur.fetchone()[0]

if not exists:
    print("Building fwd_returns table (per-stock streaming)...")
    cur.execute("""
        CREATE TABLE fwd_returns (
            ts_code VARCHAR(32),
            trade_date DATE,
            fwd_5d DOUBLE PRECISION,
            fwd_10d DOUBLE PRECISION,
            fwd_20d DOUBLE PRECISION,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    
    # Get all stock codes
    cur.execute("SELECT DISTINCT ts_code FROM daily WHERE trade_date >= '2023-12-01' ORDER BY ts_code")
    codes = [r[0] for r in cur]
    total = len(codes)
    print(f"  {total} stocks to process")
    
    for i, code in enumerate(codes):
        cur.execute("""
            SELECT trade_date, close FROM daily 
            WHERE ts_code = %s AND trade_date >= '2023-12-01'
            ORDER BY trade_date
        """, (code,))
        rows = cur.fetchall()
        if len(rows) < 22:  # need at least 20d lookahead
            continue
        
        dates = [r[0] for r in rows]
        closes = [float(r[1]) for r in rows if r[1] is not None]
        
        if len(closes) != len(dates):
            continue
        
        n = len(closes)
        insert_rows = []
        for j in range(n):
            fwds = {}
            for h in HORIZONS:
                if j + h < n:
                    fwds[f'fwd_{h}d'] = (closes[j+h] / closes[j] - 1) * 100
            if len(fwds) == len(HORIZONS):
                insert_rows.append((code, dates[j], fwds['fwd_5d'], fwds['fwd_10d'], fwds['fwd_20d']))
        
        if insert_rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO fwd_returns (ts_code, trade_date, fwd_5d, fwd_10d, fwd_20d) VALUES %s",
                insert_rows
            )
        
        if (i + 1) % 500 == 0:
            conn.commit()
            print(f"  {i+1}/{total} stocks...")
    
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM fwd_returns")
    print(f"  done: {cur.fetchone()[0]} rows")
else:
    cur.execute("SELECT COUNT(*) FROM fwd_returns")
    print(f"fwd_returns already exists: {cur.fetchone()[0]} rows")

# ── Step 2: Get dates ──
print("Getting dates from factor table...")
cur.execute("""
    SELECT DISTINCT f.trade_date FROM factor_technical_wide f
    WHERE f.price_position IS NOT NULL 
    ORDER BY f.trade_date
""")
dates = [row[0] for row in cur]
print(f"  {len(dates)} dates")

# ── Step 3: Date-by-date stratification ──
print("Running stratified backtest...")

results = {h: {q: [] for q in range(N_QUANTILES)} for h in HORIZONS}

for i, dt in enumerate(dates):
    cur.execute("""
        SELECT f.price_position, fr.fwd_5d, fr.fwd_10d, fr.fwd_20d
        FROM factor_technical_wide f
        JOIN fwd_returns fr ON f.ts_code = fr.ts_code AND f.trade_date = fr.trade_date
        WHERE f.trade_date = %s AND f.price_position IS NOT NULL
    """, (dt,))
    rows = cur.fetchall()
    
    if len(rows) < MIN_STOCKS:
        continue
    
    pp_values = np.array([float(r[0]) for r in rows])
    fwd_arr = np.array([[float(r[1]), float(r[2]), float(r[3])] for r in rows])
    
    # Quantile boundaries
    q_bounds = np.percentile(pp_values, np.linspace(0, 100, N_QUANTILES + 1))
    q_bounds[0] = -np.inf
    q_bounds[-1] = np.inf
    
    for q in range(N_QUANTILES):
        mask = (pp_values > q_bounds[q]) & (pp_values <= q_bounds[q+1])
        if mask.sum() < 10:
            continue
        for hi, h in enumerate(HORIZONS):
            q_ret = np.mean(fwd_arr[mask, hi])
            results[h][q].append(q_ret)
    
    if (i + 1) % 100 == 0:
        print(f"  {i+1}/{len(dates)} dates")

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
print("Q0 = lowest price_position (close near day's low)  →  expected HIGH returns")
print(f"Q{N_QUANTILES-1} = highest price_position (close near high) → expected LOW returns")
print()

for h in HORIZONS:
    print(f"\n{'─'*60}")
    print(f"Forward {h}-day returns  │  {len(results[h][0])} trading dates")
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

conn.close()
print("\nDone.")
