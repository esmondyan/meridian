"""模拟倒卖收益"""
import sys; sys.path.insert(0, '.')
from src.storage.db import get_latest_prices_df

df = get_latest_prices_df()
active = df[(df['volume_total'] > 0) & (df['buy_limit'] > 0)].copy()
active['profit_after_tax'] = (active['spread'] - active['high'] * 0.02).clip(lower=0)

HDR = f"{'物品':<28s} {'单件利润':>8s} {'限购':>6s} {'一轮利润':>10s} {'日利润':>10s} {'月利润':>10s}"
SEP = '-' * 72

print('===RUNLIANG===')
print(HDR)
print(SEP)
for _, r in active.nlargest(10, 'volume_total').iterrows():
    limit = r['buy_limit']
    p = r['profit_after_tax']
    per_cycle = p * limit
    per_day = per_cycle * 6
    per_month = per_day * 30
    print(f"{r['name']:<28s} {p:>8,.0f} {limit:>6,d} {per_cycle:>10,.0f} {per_day:>10,.0f} {per_month:>10,.0f}")

print()
print('===ZHONGDANG===')
print(HDR)
print(SEP)
mid = active[(active['profit_after_tax'] >= 500) & (active['volume_total'] >= 50)]
for _, r in mid.nlargest(10, 'profit_after_tax').iterrows():
    limit = max(r['buy_limit'], 1)
    p = r['profit_after_tax']
    per_cycle = p * limit
    per_day = per_cycle * 4
    per_month = per_day * 30
    print(f"{r['name']:<28s} {p:>8,.0f} {limit:>6,d} {per_cycle:>10,.0f} {per_day:>10,.0f} {per_month:>10,.0f}")

print()
total_daily = sum(r['profit_after_tax'] * r['buy_limit'] * 6 for _, r in active.nlargest(10, 'volume_total').iterrows())
mid_daily = sum(r['profit_after_tax'] * max(r['buy_limit'], 1) * 4 for _, r in mid.nlargest(10, 'profit_after_tax').iterrows())
print(f"跑量Top10同时倒: 日{total_daily:,.0f}GP", end=' ')
print(f" 折合 ${total_daily/1_000_000:.2f}/天 ${total_daily*30/1_000_000:.2f}/月")
print(f"中档Top10同时倒: 日{mid_daily:,.0f}GP", end=' ')
print(f" 折合 ${mid_daily/1_000_000:.2f}/天 ${mid_daily*30/1_000_000:.2f}/月")
