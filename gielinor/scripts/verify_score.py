"""验证评分调整"""
import sys; sys.path.insert(0, '.')
from src.analyzer.price_analysis import calc_flip_probability

result = calc_flip_probability()

print('A+ items with real volume:')
for _, r in result[(result['flip_grade'] == '⭐ A+') & (result['volume_total'] > 0)].head(20).iterrows():
    limit = max(r['buy_limit'], 1)
    daily = r['profit_after_tax'] * limit * 4
    print(f"  {r['name']:<30s} score={r['flip_score']:>4.0f}  tax_profit={r['profit_after_tax']:>8,.0f}  vol={r['volume_total']:>8,d}  daily={daily:>10,.0f}")

print()
print('Mid-tier flips (profit>=500, vol>=10):')
mid = result[(result['profit_after_tax'] >= 500) & (result['volume_total'] >= 10)]
for _, r in mid.nlargest(10, 'profit_after_tax').iterrows():
    limit = max(r['buy_limit'], 1)
    daily = r['profit_after_tax'] * limit * 4
    print(f"  {r['name']:<30s} 利{r['profit_after_tax']:>8,.0f}  量{r['volume_total']:>8,d}  日{daily:>10,.0f}GP  ${daily/1_000_000:.2f}")
