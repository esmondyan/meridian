"""
运行回测验证：纸面交易 + 价差稳定性
"""
import sys, os; sys.path.insert(0, ".")
import pandas as pd

from trade_engine.strategy import StrategyEngine, StrategyConfig
from trade_engine.backtest import PaperTrader, AccuracyTracker, analyze_spread_stability

print("=" * 60)
print("📊 加载数据")
print("=" * 60)
df = pd.read_csv("data/station_jita_full.csv")

# =============================================
# 1. 纸面交易：记录提案
# =============================================
print("\n" + "=" * 60)
print("🧪 纸面交易模拟：记录提案→等待验证")
print("=" * 60)

config = StrategyConfig(min_score=50, min_profit=1000, min_depth=5)
engine = StrategyEngine(config)
props = engine.generate_station_trading_proposals(df, max_proposals=5)

trader = PaperTrader()
for p in props:
    trader.record(p)
    print(f"  📝 记录: {p.item_name} @ {p.price:,.0f} ISK × {p.volume} 件")

print(f"\n{trader.summary()}")

# =============================================
# 2. 立即验证：用最新数据检查这些提案
# =============================================
print("\n" + "=" * 60)
print("🔍 立即验证（用当前市场数据检查）")
print("=" * 60)
matched = trader.check_all(df)
print(f"  检查了 {matched} 个提案")

print(f"\n{trader.summary()}")

# =============================================
# 3. 价差稳定性分析
# =============================================
print("\n" + "=" * 60)
print("📈 价差稳定性分析（Top 20）")
print("=" * 60)

stable = analyze_spread_stability(df, top_n=20)
print(f"{'物品':35s} {'买价':>10s} {'卖价':>10s} {'价差比':>7s} {'站内评分':>8s} {'稳定分':>8s} {'综合分':>8s}")
print("-" * 88)
for _, r in stable.iterrows():
    print(f"{str(r['name'])[:33]:33s} {r['best_buy']:>10,.0f} {r['best_sell']:>10,.0f} "
          f"{r['spread_ratio']:>6.1f}% {r['station_score']:>7.0f} "
          f"{r['stability_score']:>7.1f} {r['final_score']:>7.1f}")

# =============================================
# 4. 模型推理反事实验证
# =============================================
print("\n" + "=" * 60)
print("🧠 推理验证：对比评价高分品 vs 低分品的实际表现")
print("=" * 60)

# 高分标的（稳定分高=价差可靠）
high_score = stable.head(5)
# 低分标的（有价差但稳定差）
df_bad = df[(df["bid_ask_spread"] > 0) & (df["station_score"] >= 40) & (df["station_score"] <= 55)].sort_values("station_score")
low_score = analyze_spread_stability(df_bad, top_n=5)

print("\n🟢 高稳定性标的:")
for _, r in high_score.iterrows():
    print(f"  {r['name']:35s} 价差比 {r['spread_ratio']:>6.1f}% 稳定性 {r['stability_score']:>5.1f}")

print("\n🔴 低稳定性标的:")
for _, r in low_score.iterrows():
    print(f"  {r['name']:35s} 价差比 {r['spread_ratio']:>6.1f}% 稳定性 {r['stability_score']:>5.1f} 订单比 {r['buy_order_count']}/{r['sell_order_count']}")

print("\n" + "=" * 60)
print("📊 回测数据已保存")
print(f"  纸面交易: data/papertrades/trades.csv")
print(f"")
print("  下次采集新数据时会自动验证这些提案。")
print("  积累足够数据后就能算每个模型的准确率。")
print("=" * 60)
