"""IC analysis — computes returns from close prices (pct_change is NULL)"""
import sys, os
sys.path.insert(0, '/home/hermes/projects/stocksage/alpha')
with open('/home/hermes/projects/stocksage/alpha/.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

import numpy as np, psycopg2
from datetime import date

def spearman_r(x, y):
    n = len(x)
    rx = np.argsort(np.argsort(x)).astype(float) + 1
    ry = np.argsort(np.argsort(y)).astype(float) + 1
    return 1 - 6 * np.sum((rx - ry)**2) / (n * (n*n - 1))

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password=os.environ['PG_PASSWORD'], dbname='stocksage_alpha')
cur = conn.cursor()

DATES = ['2026-05-15', '2026-05-22', '2026-05-29']
FACTORS = ['momentum_5d','momentum_20d','momentum_60d','rsi_14','ma20_deviation',
           'volume_ratio','volatility_20d','macd_histogram','atr_ratio',
           'consecutive_down','williams_r','stoch_k','max_dd_20d',
           'lower_shadow_ratio','price_position','volume_spike',
           'ma50_deviation','ma200_deviation','daily_range','turnover_proxy']

# Step 1: Load close prices and compute returns
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

# Compute returns: (close[t+5] / close[t] - 1) * 100
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
results = []
for col in FACTORS:
    ics = []
    for dt_str in DATES:
        dt = date.fromisoformat(dt_str)
        cur.execute(
            f'SELECT SPLIT_PART(ts_code,\'.\',1) as code, "{col}" as fval '
            f'FROM factor_technical_wide WHERE trade_date = %s', (dt,))
        fv, fw = [], []
        for code, fval in cur:
            if fval is None: continue
            r = fwd5.get(code, {}).get(dt)
            if r is None: continue
            fv.append(float(fval)); fw.append(r)

        n = len(fv)
        if n < 100: continue
        ics.append(spearman_r(np.array(fv), np.array(fw)))

    if len(ics) >= 2:
        a = np.array(ics)
        results.append({
            'factor': col, 'ic_mean': round(a.mean(),4),
            'ic_ir': round(a.mean()/a.std(),3) if a.std()>0 else 0,
            'pos': f'{float((a>0).mean()):.0%}', 'n': len(a)
        })

cur.close(); conn.close()
results.sort(key=lambda x: abs(x['ic_mean']), reverse=True)

print(f'\nRank IC (前向5日收益)  ★>0.02  ·>0.01  ({len(DATES)}个交易日)')
print('-'*60)
for r in results:
    bar = '█' * max(1, int(abs(r['ic_mean'])*800))
    mark = ' ★' if abs(r['ic_mean'])>0.02 else (' ·' if abs(r['ic_mean'])>0.01 else '')
    print(f"  {r['factor']:22s} IC={r['ic_mean']:+7.4f}  IR={r['ic_ir']:+6.3f}  {bar}{mark}")

n_pos = sum(1 for r in results if r['ic_mean']>0.01)
n_neg = sum(1 for r in results if r['ic_mean']<-0.01)
print(f'\n趋势因子(正IC): {n_pos}  反转因子(负IC): {n_neg}  噪声: {len(results)-n_pos-n_neg}')
print('正IC=高值→涨(趋势/动量)  负IC=低值→涨(反转/超卖)')
