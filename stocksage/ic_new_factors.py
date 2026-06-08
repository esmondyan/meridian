"""IC analysis on 10 new factors — compute fwd returns on-the-fly."""
import psycopg2, numpy as np

conn = psycopg2.connect(host='127.0.0.1', port=5432, user='hermes',
    password='hermes_quant_2026', dbname='stocksage_alpha')
cur = conn.cursor()

FACTORS = ['st_rev_20d','max_ret_20d','ivol_20d','mom_12m_1m','amihud_illiq',
           'ln_cap','pb','pe_ttm','roe','roa']

# Quick check data
for f in FACTORS[:2]:
    cur.execute("SELECT COUNT(*) FROM factor_technical_wide WHERE \"%s\" IS NOT NULL" % f)
    print("%s: %s non-null" % (f, f'{cur.fetchone()[0]:,}'))

# Load close prices for forward returns
print("\nLoading close prices...")
cur.execute("SELECT ts_code, trade_date, close FROM daily WHERE trade_date >= '2024-06-01' AND close IS NOT NULL ORDER BY ts_code, trade_date")
close_map = {}
for code, dt, close in cur:
    close_map.setdefault(code, {})[dt] = float(close)
print("  %d stocks" % len(close_map))

# Compute forward 5-day returns
print("Computing fwd returns...")
fwd5 = {}
for code, d in close_map.items():
    dates = sorted(d.keys())
    for i, dt in enumerate(dates):
        if i + 5 < len(dates):
            ret = (d[dates[i+5]] / d[dt] - 1) * 100
            fwd5.setdefault(code, {})[dt] = ret
del close_map
n = sum(len(v) for v in fwd5.values())
print("  %d fwd5 obs" % n)

# Sample dates over last ~6 months 
cur.execute("""
    SELECT DISTINCT trade_date FROM factor_technical_wide 
    WHERE st_rev_20d IS NOT NULL AND trade_date >= '2024-12-01'
    ORDER BY trade_date
""")
all_dates = [r[0] for r in cur.fetchall()]
sample_dates = all_dates[::10]  # every 10th
print("%d sample dates (from %d total)" % (len(sample_dates), len(all_dates)))

def spearman_r(x, y):
    n = len(x)
    if n < 30: return 0
    rx = np.argsort(np.argsort(x)).astype(float) + 1
    ry = np.argsort(np.argsort(y)).astype(float) + 1
    return 1 - 6 * np.sum((rx - ry)**2) / (n * (n*n - 1))

print("\n%-20s %8s %8s %6s  %s" % ('Factor','IC_Mean','IC_Std','N','Direction'))
print("-" * 60)
results = []
for factor in FACTORS:
    ics = []
    for dt in sample_dates:
        cur.execute("SELECT ts_code, \"%s\"::float FROM factor_technical_wide WHERE trade_date=%%s AND \"%s\" IS NOT NULL" % (factor, factor), (dt,))
        rows = cur.fetchall()
        fv, fw = [], []
        for code, val in rows:
            if code in fwd5 and dt in fwd5[code]:
                fv.append(float(val))
                fw.append(fwd5[code][dt])
        if len(fv) >= 100:
            ic = spearman_r(np.array(fv), np.array(fw))
            ics.append(ic)
    
    if ics:
        ic_mean = np.mean(ics)
        ic_std = np.std(ics, ddof=1) if len(ics) > 1 else 0
        direction = '趋势(买高)' if ic_mean > 0 else '反转(买低)'
        tag = '🔥' if abs(ic_mean)>=0.05 else ('⚠️' if abs(ic_mean)>=0.03 else '')
        print("%-20s %8.4f %8.4f %6d  %s %s" % (factor, ic_mean, ic_std, len(ics), direction, tag))
        results.append((factor, ic_mean, ic_std, len(ics)))
    else:
        print("%-20s %8s %8s %6s  no valid data" % (factor, '-', '-', '-'))

results.sort(key=lambda x: abs(x[1]), reverse=True)
print("\n--- Ranked by |IC| ---")
for name, ic, std, n in results:
    bar = '█' * int(abs(ic) * 100)
    print("  %-22s %+7.4f  %s" % (name, ic, bar))

conn.close()
