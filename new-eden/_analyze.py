import pandas as pd
df = pd.read_csv('data/jita_latest.csv')

print(f'=== 即时套利（买单 > 卖价）===')
inverted = df[df['spread'] > 0].sort_values('spread', ascending=False)
print(f'共 {len(inverted)} 个')
for _, r in inverted.iterrows():
    print(f'  {r["name"]:30s} 卖={r["best_sell"]:>12,.0f} 买={r["best_buy"]:>12,.0f} 差价={r["spread"]:>12,.0f} 深度={min(r["best_buy_vol"], r["best_sell_vol"]):>6,} 评分={r["eve_score"]}')

print()
print('=== 窄价差物品（价差 < 5%，挂单套利机会）===')
tight = df[(df['spread'] <= 0) & (abs(df['spread']) < df['best_sell']*0.05)].sort_values('spread', ascending=False)
print(f'共 {len(tight)} 个')
for _, r in tight.head(20).iterrows():
    pct = abs(r['spread']) / r['best_sell'] * 100
    print(f'  {r["name"]:30s} 卖={r["best_sell"]:>12,.0f} 买={r["best_buy"]:>12,.0f} 差距={r["spread"]:>12,.0f} ({pct:.2f}%) 深度={min(r["best_buy_vol"], r["best_sell_vol"]):>6,}')

print()
print('=== 订单深度 Top 10 ===')
top = df.nlargest(10, 'total_buy_vol')
for _, r in top.iterrows():
    print(f'  {r["name"]:30s} 买总量={r["total_buy_vol"]:>8,} 卖总量={r["total_sell_vol"]:>8,} 最优买量={r["best_buy_vol"]:>6,}')
