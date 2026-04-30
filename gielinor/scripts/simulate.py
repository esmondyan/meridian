"""模拟倒卖回测"""
import sys; sys.path.insert(0, '.')
from src.storage.db import get_latest_prices_df

df = get_latest_prices_df()
active = df[(df['volume_total'] > 10) & (df['buy_limit'] > 0)].copy()
active['profit_after_tax'] = (active['spread'] - active['high'] * 0.02).clip(lower=0)
active = active[active['profit_after_tax'] > 0]

for capital in [500_000, 1_000_000, 5_000_000, 10_000_000]:
    candidates = active[active['high'] <= capital].sort_values('profit_after_tax', ascending=False)
    print(f'\n本金 {capital:>10,.0f} GP  |  可倒 {len(candidates)} 件物品')
    print(f'{"物品":<30s} {"单价":>8s} {"利润":>6s} {"ROI":>5s} {"日利润":>10s} {"折合":>8s}')
    print('-' * 67)
    for _, r in candidates.head(5).iterrows():
        limit = max(r['buy_limit'], 1)
        can_buy = min(limit, int(capital / r['high']))
        per_flip = r['profit_after_tax'] * can_buy
        roi = r['profit_after_tax'] / r['high'] * 100
        daily = per_flip * 4
        usd = daily / 1_000_000
        print(f"{r['name']:<30s} {r['high']:>8,.0f} {r['profit_after_tax']:>6,.0f} {roi:>4.1f}% {daily:>10,.0f} ${usd:>6.2f}")
