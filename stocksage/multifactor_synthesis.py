"""
Multi-factor synthesis: correlation matrix → composite score → backtest.
Uses published factor directions (no IC recomputation needed).
"""
import psycopg2, numpy as np, sys
from datetime import date, timedelta

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password='hermes_quant_2026', dbname='stocksage_alpha')
cur = conn.cursor()

# ── Factor selection with published directions ──
# (factor, sign: 1=higher is better, -1=lower is better)
FACTORS = [
    ('st_rev_20d', -1),      # short-term reversal: lower past return → higher fwd
    ('max_ret_20d', -1),     # MAX effect: lottery stocks underperform
    ('ivol_20d', -1),        # IVOL puzzle: high vol → low return
    ('mom_12m_1m', 1),       # 12-1 momentum: trend continuation
    ('amihud_illiq', 1),     # illiquidity premium
    ('ln_cap', -1),          # size: small-cap premium
    ('pb', -1),              # value: low PB
    ('pe_ttm', -1),          # value: low PE
    ('roe', 1),              # quality: high ROE
    ('roa', 1),              # quality: high ROA
]

# Include previously validated factors
MORE = [
    ('consecutive_down', -1),  # downside momentum: NOT reversal in A-shares
    ('williams_r', -1),        # oversold bounce
    ('stoch_k', 1),            # stochastic: oversold → bounce (but IC=-0.039 means negative)
    ('volatility_20d', -1),    # low vol anomaly
    ('macd_histogram', 1),     # MACD trend
]

# Actually, let's be more careful. From our IC results:
# consecutive_down: IC=+0.065 → higher consecutive down → HIGHER return
#   But we interpreted this as "trend continuation". Wait, positive IC means
#   more consecutive down → higher return. That's actually REVERSAL.
#   Hmm, let me re-check: IC=+0.065. consecutive_down counts consecutive 
#   declining days. Higher consecutive_down → higher fwd return.
#   That means: stocks that have fallen MORE → bounce MORE. That's reversal!
#   But we labeled it "趋势延续". That was wrong.

# Let me use the published directions instead of our possibly-mislabeled IC:
# consecutive_down: many sources say A-share short-term is reversal (跌多了反弹)
#   So sign = 1 (higher consecutive_down → buy)
# williams_r: IC=-0.039, higher WR → lower return. But WR is inverted (-100 to 0).
#   -80 means oversold. Higher WR (closer to 0) means overbought → lower return.
#   So sign = -1 (higher WR → sell). Or equivalently, sign = 1 for raw WR.
# Actually, williams_r formula: (high14-close)/(high14-low14)*-100. 
#   Higher WR means closer to 0 (overbought). IC=-0.039 means higher WR → lower return.
#   So sign = -1 (we want LOW WR = oversold). 
# stoch_k: IC=-0.039. Higher stoch_k → overbought → lower return. sign = -1.

# Let me just use a clean set with correct published directions:
ALL_FACTORS = [
    # New factors (from published research)
    ('st_rev_20d', -1),       # reversal: past losers → future winners
    ('max_ret_20d', -1),      # lottery: high max ret → future losers  
    ('ivol_20d', -1),         # IVOL: high idiosyncratic vol → losers
    ('mom_12m_1m', 1),        # momentum: past winners → continue
    ('amihud_illiq', 1),      # illiquidity premium
    ('ln_cap', -1),           # size: small beats large
    ('pb', -1),               # value: cheap beats expensive
    ('pe_ttm', -1),           # value: cheap beats expensive
    ('roe', 1),               # quality: profitable beats unprofitable
    ('roa', 1),               # quality
    # Existing validated factors
    ('consecutive_down', 1),  # A-share: fallen stocks bounce (reversal at short horizon)
    ('volatility_20d', -1),   # low vol anomaly
    ('williams_r', -1),       # WR is inverted: -100=oversold(buy), 0=overbought(sell)
]

print("Factors: %d" % len(ALL_FACTORS))
for name, sign in ALL_FACTORS:
    direction = '买高' if sign > 0 else '买低'
    print("  %-22s  %s" % (name, direction))

# ── Get dates with good coverage ──
print("\nLoading dates...")
cur.execute("""
    SELECT trade_date FROM factor_technical_wide
    WHERE st_rev_20d IS NOT NULL AND ln_cap IS NOT NULL
    AND trade_date >= '2024-06-01'
    GROUP BY trade_date HAVING COUNT(*) >= 500
    ORDER BY trade_date
""")
DATES = [r[0] for r in cur.fetchall()]
print("  %d dates with >=500 stocks" % len(DATES))

if len(DATES) < 50:
    # Try lower threshold
    cur.execute("""
        SELECT trade_date FROM factor_technical_wide
        WHERE st_rev_20d IS NOT NULL AND ln_cap IS NOT NULL
        AND trade_date >= '2024-06-01'
        GROUP BY trade_date HAVING COUNT(*) >= 100
        ORDER BY trade_date
    """)
    DATES = [r[0] for r in cur.fetchall()]
    print("  %d dates with >=100 stocks" % len(DATES))

# ── Pre-load close prices for backtest ──
print("Loading close prices for backtest...")
min_date = DATES[0] - timedelta(days=60)
max_date = DATES[-1] + timedelta(days=60)
cur.execute("SELECT ts_code, trade_date, close FROM daily WHERE trade_date >= %s AND trade_date <= %s AND close IS NOT NULL ORDER BY ts_code, trade_date",
    (min_date, max_date))

# Store as arrays per stock for efficient forward return computation
close_data = {}  # {code: (dates_list, closes_list)}
count = 0
for code, dt, close in cur:
    if code not in close_data:
        close_data[code] = ([], [])
    close_data[code][0].append(dt)
    close_data[code][1].append(float(close))
    count += 1
print("  %d stocks, %d rows" % (len(close_data), count))

# Helper: get forward return for a stock on a date
def get_fwd_ret(code, dt, horizon=5):
    if code not in close_data: return None
    dates, closes = close_data[code]
    try:
        idx = dates.index(dt)
    except ValueError:
        return None
    if idx + horizon >= len(dates):
        return None
    return (closes[idx + horizon] / closes[idx] - 1) * 100

# ── Step 1: Correlation matrix ──
print("\n=== Correlation Matrix ===")
factor_names = [f[0] for f in ALL_FACTORS]
n_factors = len(factor_names)

# Sample dates for correlation
corr_dates = DATES[::len(DATES)//20 + 1][:20]  # ~20 sample dates

all_vals = {f: [] for f in factor_names}
for dt in corr_dates:
    cols = ', '.join(['"%s"::float' % f for f in factor_names])
    cur.execute("SELECT %s FROM factor_technical_wide WHERE trade_date=%%s AND st_rev_20d IS NOT NULL AND ln_cap IS NOT NULL" % cols, (dt,))
    rows = cur.fetchall()
    for i, f in enumerate(factor_names):
        vals = [float(r[i]) for r in rows if r[i] is not None]
        all_vals[f].extend(vals[:500])  # cap per date

# Compute pairwise correlations
corr = np.zeros((n_factors, n_factors))
for i in range(n_factors):
    for j in range(i, n_factors):
        vi = np.array(all_vals[factor_names[i]])
        vj = np.array(all_vals[factor_names[j]])
        n = min(len(vi), len(vj))
        if n < 30:
            corr[i,j] = corr[j,i] = 0
            continue
        # Spearman
        ri = np.argsort(np.argsort(vi[:n])).astype(float) + 1
        rj = np.argsort(np.argsort(vj[:n])).astype(float) + 1
        c = 1 - 6 * np.sum((ri - rj)**2) / (n * (n*n - 1))
        corr[i,j] = corr[j,i] = c

print("%-20s" % '', end='')
for f in factor_names:
    print("%7s" % f[:7], end='')
print()
for i, f in enumerate(factor_names):
    print("%-20s" % f, end='')
    for j in range(n_factors):
        v = corr[i,j]
        if i == j:
            print("%7s" % '1.00', end='')
        elif abs(v) > 0.5:
            print("\033[91m%7.2f\033[0m" % v, end='')  # red for high corr
        else:
            print("%7.2f" % v, end='')
    print()

# ── Step 2: Composite score + backtest ──
print("\n=== Backtest: Top 20% equally-weighted ===")

TOP_PCT = 0.20
results = []  # list of (date, daily_return%)

for dt in DATES:
    # Load factor values
    cols = ', '.join(['ts_code'] + ['"%s"::float' % f[0] for f in ALL_FACTORS])
    cur.execute("SELECT %s FROM factor_technical_wide WHERE trade_date=%%s AND st_rev_20d IS NOT NULL AND ln_cap IS NOT NULL" % cols, (dt,))
    rows = cur.fetchall()
    if len(rows) < 100:
        continue
    
    codes = [r[0] for r in rows]
    n = len(rows)
    
    # Build factor matrix
    factor_mat = np.zeros((n, n_factors))
    for i in range(n_factors):
        vals = [float(r[i+1]) if r[i+1] is not None else np.nan for r in rows]
        col = np.array(vals)
        # Z-score (ignore NaN)
        mask = ~np.isnan(col)
        if mask.sum() < 10:
            factor_mat[:, i] = 0
        else:
            mu = np.mean(col[mask])
            sigma = np.std(col[mask])
            if sigma > 0:
                col[mask] = (col[mask] - mu) / sigma
            col[~mask] = 0
            factor_mat[:, i] = col
    
    # Composite score: sign-adjusted z-scores, equal weight
    signs = np.array([f[1] for f in ALL_FACTORS])
    composite = factor_mat @ signs  # sign already accounts for direction
    composite = composite / n_factors  # equal weight
    
    # Select top 20%
    cutoff = int(n * (1 - TOP_PCT))
    top_idx = np.argsort(composite)[cutoff:]
    top_codes = [codes[i] for i in top_idx]
    
    # Compute equal-weighted forward return
    rets = []
    for code in top_codes:
        r = get_fwd_ret(code, dt, horizon=5)
        if r is not None:
            rets.append(r)
    
    if len(rets) >= 10:
        avg_ret = np.mean(rets)
        results.append((dt, avg_ret))
    
    if len(results) % 20 == 0:
        cum = np.prod(1 + np.array([r[1] for r in results]) / 100)
        print("  %s: %d dates, cum_ret=%.1f%%" % (dt, len(results), (cum-1)*100))

# ── Performance stats ──
if not results:
    print("No valid results!")
    conn.close()
    sys.exit(1)

rets = np.array([r[1] for r in results])
cum = np.cumprod(1 + rets / 100)
total_ret = (cum[-1] - 1) * 100
ann_ret = total_ret / len(results) * 250  # rough annualized
vol = np.std(rets, ddof=1) * np.sqrt(250 / 5)  # annualized (5-day returns)
sharpe = ann_ret / vol if vol > 0 else 0
peak = np.maximum.accumulate(cum)
dd = (cum - peak) / peak * 100
max_dd = np.min(dd)
hit_rate = np.sum(rets > 0) / len(rets) * 100

print("\n" + "="*50)
print("MULTI-FACTOR BACKTEST RESULTS")
print("="*50)
print("Period: %s to %s" % (results[0][0], results[-1][0]))
print("N dates: %d" % len(results))
print("Total return: %.1f%%" % total_ret)
print("Ann. return: %.1f%%" % ann_ret)
print("Ann. volatility: %.1f%%" % vol)
print("Sharpe: %.2f" % sharpe)
print("Max drawdown: %.1f%%" % max_dd)
print("Hit rate: %.1f%%" % hit_rate)

# Top/bottom factor contributions
print("\nFactor contributions (avg z-score in top quintile):")
contribs = []
for i, (fname, sign) in enumerate(ALL_FACTORS):
    contribs.append((fname, sign * np.mean(factor_mat[top_idx, i] if 'top_idx' in dir() else 0)))
contribs.sort(key=lambda x: abs(x[1]), reverse=True)
for name, val in contribs[:10]:
    bar = '█' * int(abs(val) * 5)
    print("  %-22s %+6.2f  %s" % (name, val, bar))

conn.close()
