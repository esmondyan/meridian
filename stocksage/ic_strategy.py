"""IC analysis for strategy-derived factors. Reuses the ic_light.py pattern."""
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

def spearman_r(x, y):
    n = len(x)
    rx = np.argsort(np.argsort(x)).astype(float) + 1
    ry = np.argsort(np.argsort(y)).astype(float) + 1
    return 1 - 6 * np.sum((rx - ry)**2) / (n * (n*n - 1))

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')
cur = conn.cursor()

# Use same dates as original IC analysis
DATES = ['2026-05-15', '2026-05-22', '2026-05-29']
STRATEGY_FACTORS = [
    'limit_up_flag', 'days_since_limit_up', 'pullback_from_limit_up',
    'yang_today', 'yin_streak', 'yang_after_yin_streak',
    'ma10_ma54_platform',
]

# Step 1: Load close prices and compute forward 5-day returns
print("Loading close prices...")
cur.execute("""
    SELECT ts_code, trade_date, close FROM daily
    WHERE trade_date >= '2026-05-15' AND trade_date <= '2026-06-05'
    ORDER BY ts_code, trade_date
""")

close_map = {}
for code, dt, close in cur:
    if close is None: continue
    close_map.setdefault(code, {})[dt] = float(close)

fwd5 = {}
for code, d in close_map.items():
    dates = sorted(d.keys())
    for i, dt in enumerate(dates):
        if i + 5 < len(dates):
            ret5 = (d[dates[i+5]] / d[dt] - 1) * 100
            fwd5.setdefault(code, {})[dt] = ret5
del close_map
print(f"  {len(fwd5)} stocks with forward returns")

# Step 2: For each factor, compute IC
print("\nStrategy Factor IC Analysis (Spearman, 5-day forward)")
print("=" * 60)
print(f"{'Factor':<28} {'IC Mean':>8} {'N_dates':>8} {'Conclusion'}")
print("-" * 60)

results = []
for col in STRATEGY_FACTORS:
    ics = []
    n_valid = 0
    for dt_str in DATES:
        dt = date.fromisoformat(dt_str)
        
        cur.execute(f"""
            SELECT f.ts_code, f."{col}"
            FROM factor_technical_wide f
            WHERE f.trade_date = %s AND f."{col}" IS NOT NULL
        """, (dt,))
        rows = cur.fetchall()
        
        if len(rows) < 50:
            continue
        
        xs = []
        ys = []
        for code, fval in rows:
            if code in fwd5 and dt in fwd5[code]:
                xs.append(float(fval))
                ys.append(fwd5[code][dt])
        
        # Filter inf/nan
        valid = [(x, y) for x, y in zip(xs, ys) if np.isfinite(x) and np.isfinite(y)]
        if len(valid) < 50:
            continue
        
        xs, ys = zip(*valid)
        ic = spearman_r(np.array(xs), np.array(ys))
        ics.append(ic)
        n_valid += 1
    
    if ics:
        ic_mean = np.mean(ics)
        ic_std = np.std(ics, ddof=1) if len(ics) > 1 else 0
        
        # Interpret
        if abs(ic_mean) >= 0.05:
            direction = "趋势延续" if ic_mean > 0 else "反转"
            conclusion = f"🔥 强{direction}信号"
        elif abs(ic_mean) >= 0.03:
            direction = "趋势延续" if ic_mean > 0 else "反转"
            conclusion = f"中{direction}"
        elif abs(ic_mean) >= 0.015:
            conclusion = "弱信号"
        else:
            conclusion = "无效"
        
        print(f"{col:<28} {ic_mean:>8.4f} {n_valid:>8}  {conclusion}")
        results.append((col, ic_mean, ic_std, n_valid))
    else:
        print(f"{col:<28} {'N/A':>8} {'0':>8}  no valid data")

# Summary sort
results.sort(key=lambda x: abs(x[1]), reverse=True)
print("\n--- Ranking by |IC| ---")
for name, ic, std, n in results:
    bar = '█' * int(abs(ic) * 200)
    print(f"  {name:<28} {ic:+.4f} {bar}")

conn.close()
