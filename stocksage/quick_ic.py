"""Quick IC analysis — runs in venv"""
import sys, os
sys.path.insert(0, '/home/hermes/projects/stocksage/alpha')

# Load env
with open('/home/hermes/projects/stocksage/alpha/.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

import numpy as np, pandas as pd
from scipy import stats
from sqlalchemy import text
from src.data.db import get_engine

engine = get_engine()
DATES = ['2026-05-20', '2026-05-26', '2026-05-28', '2026-06-01', '2026-06-03']

with engine.connect() as conn:
    fcols = [r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='factor_technical_wide' AND column_name NOT IN ('ts_code','trade_date') ORDER BY ordinal_position"
    )).fetchall()]
    all_cols = [r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='factor_technical_wide' ORDER BY ordinal_position"
    )).fetchall()]

    results = {}
    for dt in DATES:
        rows = conn.execute(text("""
            SELECT f.*, fwd.pct_change as fwd5
            FROM factor_technical_wide f
            JOIN daily fwd ON SPLIT_PART(f.ts_code, '.', 1) = fwd.ts_code 
                AND fwd.trade_date = (SELECT MIN(trade_date) FROM daily d2
                    WHERE d2.ts_code = fwd.ts_code AND d2.trade_date > :dt
                    OFFSET 4)
            WHERE f.trade_date = :dt
        """), {'dt': dt}).fetchall()
        if not rows: continue
        df = pd.DataFrame(rows, columns=all_cols + ['fwd5'])
        print(f"  {dt}: {len(df)} stocks")
        for col in fcols:
            gv = df[[col, 'fwd5']].dropna()
            if len(gv) < 100: continue
            ic, _ = stats.spearmanr(gv[col], gv['fwd5'])
            results.setdefault(col, []).append(ic)

summary = []
for col, ics in results.items():
    if len(ics) < 2: continue
    a = np.array(ics)
    summary.append({'factor': col, 'ic_mean': a.mean(), 'ic_std': a.std(),
                    'ic_ir': a.mean()/a.std() if a.std()>0 else 0, 
                    'pos': (a>0).mean(), 'n': len(a)})

ic = pd.DataFrame(summary)
ic['abs_ic'] = ic['ic_mean'].abs()
ic = ic.sort_values('abs_ic', ascending=False)

print(f'\nRank IC (前向5日) — {len(DATES)}个交易日  ★>0.02  ·>0.01')
print('-'*70)
for i, (_, r) in enumerate(ic.iterrows()):
    bar = chr(9608) * max(1, int(r['abs_ic'] * 800))
    mark = ' ★' if r['abs_ic'] > 0.02 else (' ·' if r['abs_ic'] > 0.01 else '')
    print(f"{i+1:2d}. {r['factor']:22s} IC={r['ic_mean']:+7.4f}  IR={r['ic_ir']:+6.3f}  pos={r['pos']:.0%}  {bar}{mark}")

print(f"\n|IC|>0.02: {(ic['abs_ic']>0.02).sum()}  |IC|>0.01: {(ic['abs_ic']>0.01).sum()}  总:{len(ic)}")
