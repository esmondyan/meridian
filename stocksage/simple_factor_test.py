"""单因子回测：一次查询，Python 汇总"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
from src.data.db import get_session
from sqlalchemy import text

s = get_session()

# 一次查询，计算所有窗口指标
print("查询中...")
rows = s.execute(text("""
    SELECT ts_code, trade_date, close,
           LAG(close, 1) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_close,
           LAG(close, 5) OVER (PARTITION BY ts_code ORDER BY trade_date) AS close_5d_ago,
           LEAD(close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS next_close
    FROM daily
    WHERE trade_date BETWEEN '2024-05-15' AND '2026-04-30'
    ORDER BY ts_code, trade_date
""")).fetchall()
print(f"  数据: {len(rows)} 行")

df = pd.DataFrame(rows, columns=['ts_code', 'trade_date', 'close', 'prev_close', 'close_5d_ago', 'next_close'])
df = df.dropna(subset=['close_5d_ago', 'next_close'])

df['ret_5d'] = df['close'] / df['close_5d_ago'] - 1
df['next_ret'] = df['next_close'] / df['close'] - 1

# 筛：5日跌>15%，非跌停
mask = (df['ret_5d'] < -0.15) & (df['close'] > df['prev_close'] * 0.90)
candidates = df[mask].copy()

# 按日汇总
daily = candidates.groupby('trade_date')['next_ret'].agg(['mean', 'count'])
daily = daily[daily['count'] >= 5]  # 至少5只

daily['net'] = daily['mean'] - 0.0015  # 扣成本

daily['cum'] = (1 + daily['net']).cumprod() - 1

total_ret = float(daily['cum'].iloc[-1])
days = len(daily)
ann = (1 + total_ret) ** (252 / days) - 1
sharpe = float(np.sqrt(252) * daily['net'].mean() / daily['net'].std())
win = float((daily['net'] > 0).sum() / days * 100)
avg_buy = daily['count'].mean()

print(f"""
=== 最简策略：每天等权买所有 ret_5d < -15% 股票 ===
  日均买入: {avg_buy:.0f} 只
  毛利率均值: {daily['mean'].mean()*100:+.2f}%
  净利均值:   {daily['net'].mean()*100:+.2f}%
  胜率:       {win:.1f}%
  累计净收益: {total_ret*100:.2f}%
  年化:       {ann*100:.2f}%
  夏普:       {sharpe:.2f}
""")

# 分年看
daily['year'] = daily.index.year
for yr, grp in daily.groupby('year'):
    cum_yr = (1 + grp['net']).cumprod().iloc[-1] - 1
    print(f"  {yr}: 累计={cum_yr*100:+.1f}%  日均净利={grp['net'].mean()*100:+.2f}%  胜率={(grp['net']>0).sum()/len(grp)*100:.0f}%")

s.close()
