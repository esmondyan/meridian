"""
端到端测试：站内挂单数据 + 策略引擎 + 真实 API 评审
"""
import sys, os
sys.path.insert(0, ".")

from trade_engine.strategy import StrategyEngine, StrategyConfig
from trade_engine.evaluator import run_committee, create_standard_committee
from trade_engine.consensus import ConsensusEngine
import pandas as pd

print("=" * 60)
print("📊 加载站内挂单数据")
print("=" * 60)
df = pd.read_csv("data/station_jita_full.csv")
print(f"总物品: {len(df):,}")
print(f"正常市场: {len(df[df['bid_ask_spread'] > 0]):,}")
print(f"优质标的 (评分>=60): {len(df[df['station_score'] >= 60]):,}")
print(f"深度 >= 10 且有价差 >= 3%: {len(df[(df['spread_ratio'] >= 3) & (df['best_buy_vol'] >= 10)]):,}")
print()

print("=" * 60)
print("🧠 策略引擎生成提案")
print("=" * 60)
config = StrategyConfig(min_score=50, min_profit=5000, min_depth=5)
engine = StrategyEngine(config)
props = engine.generate_station_trading_proposals(df, max_proposals=3)
print(f"生成了 {len(props)} 个提案\n")
for i, p in enumerate(props, 1):
    print(f"  #{i}: {p.summary()}")
    print()

if not props:
    print("没有提案，调整筛选条件...")
    config = StrategyConfig(min_score=40, min_profit=1000, min_depth=2)
    engine = StrategyEngine(config)
    props = engine.generate_station_trading_proposals(df, max_proposals=3)
    print(f"放宽后生成了 {len(props)} 个提案\n")
    for i, p in enumerate(props, 1):
        print(f"  #{i}: {p.summary()}")
        print()

print("=" * 60)
print("🤖 多模型评审（模拟评估器）")
print("=" * 60)
if props:
    committee = create_standard_committee(api_key="")
    for prop in props:
        evals = run_committee(prop, evaluators=committee)
        consensus = ConsensusEngine().aggregate(prop, evals)
        print(f"\n{consensus.short_report()}")
        for e in evals:
            print(f"  [{e.model_name:30s}] {e.score:5.0f}分 {e.verdict:7s} 置信度{e.confidence:.0%}")

# 用真实 API 跑前 1 个
api_key = os.environ.get("DEEPSEEK_API_KEY", "")
if api_key and props:
    print()
    print("=" * 60)
    print("🤖 多模型评审（真实 DeepSeek API）")
    print("=" * 60)
    committee = create_standard_committee(api_key)
    prop = props[0]
    print(f"\n评审: {prop.item_name}")
    evals = run_committee(prop, evaluators=committee)
    consensus = ConsensusEngine().aggregate(prop, evals)
    print(f"\n{consensus.full_report()}")

print("\n✅ 测试完成!")
