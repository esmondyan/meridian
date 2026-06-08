"""IC 分析 — 完全在 PostgreSQL 内计算，零内存压力"""
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

with engine.connect() as conn:
    # Get factor columns dynamically
    fcols = [r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='factor_technical_wide' "
        "AND column_name NOT IN ('ts_code','trade_date') ORDER BY ordinal_position"
    )).fetchall()]

    # Build a single query with all factor-forward correlations
    # Strategy: for each trading day, compute corr between factor and forward 5d return
    # Then average across days
    
    print("Computing ICs via per-date SQL corr()...")

results = {}
with engine.connect() as conn:
    dates = [r[0] for r in conn.execute(text(
        "SELECT DISTINCT trade_date FROM factor_technical_wide "
        "WHERE trade_date >= '2026-04-15' AND trade_date <= '2026-06-03' "
        "ORDER BY trade_date"
    )).fetchall()]
    
    usable_dates = dates[:-5]  # skip last 5 (no forward return)
    print(f"  {len(usable_dates)} dates to process")
    
    for col in fcols:
        ic_vals = []
        for dt in usable_dates:
            # SQL corr between factor value and forward 5d return
            r = conn.execute(text(f"""
                SELECT corr(f.fval, fwd.fwd5)
                FROM (
                    SELECT SPLIT_PART(ts_code,'.',1) as code, "{col}" as fval
                    FROM factor_technical_wide WHERE trade_date = :dt
                ) f
                JOIN (
                    SELECT ts_code as code, pct_change as fwd5 FROM daily
                    WHERE trade_date = (
                        SELECT MIN(trade_date) FROM daily d2
                        WHERE d2.ts_code = daily.ts_code AND d2.trade_date > :dt
                        OFFSET 4
                    )
                ) fwd ON f.code = fwd.code
                WHERE f.fval IS NOT NULL AND fwd.fwd5 IS NOT NULL
            """), {'dt': dt}).scalar()
            
            if r is not None and not (np.isnan(r) if isinstance(r, float) else False):
                ic_vals.append(r)
        
        if len(ic_vals) >= 5:
            a = np.array(ic_vals)
            results[col] = {
                'ic_mean': float(a.mean()),
                'ic_std': float(a.std()),
                'ic_ir': float(a.mean()/a.std()) if a.std() > 0 else 0,
                'pos': float((a > 0).mean()),
                'n': len(a),
            }

# Print results
ic = pd.DataFrame(results).T
ic.index.name = 'factor'
ic = ic.reset_index()
ic['abs_ic'] = ic['ic_mean'].abs()
ic = ic.sort_values('abs_ic', ascending=False)

print(f'\nRank IC (前向5日收益) — ★>0.02  ·>0.01')
print('-'*70)
for i, (_, r) in enumerate(ic.iterrows()):
    bar = chr(9608) * max(1, int(r['abs_ic'] * 800))
    mark = ' ★' if r['abs_ic'] > 0.02 else (' ·' if r['abs_ic'] > 0.01 else '')
    print(f"{i+1:2d}. {r['factor']:22s} IC={r['ic_mean']:+7.4f}  IR={r['ic_ir']:+6.3f}  "
          f"pos={r['pos']:.0%}  n={r['n']}d  {bar}{mark}")

print(f"\n|IC|>0.02: {(ic['abs_ic']>0.02).sum()}  |IC|>0.01: {(ic['abs_ic']>0.01).sum()}  总:{len(ic)}")
print("\n关键发现:")
print("  正IC = 因子值越高，未来收益越高（动量/趋势类）")
print("  负IC = 因子值越低，未来收益越高（反转/超卖类）")
