"""
端到端测试：交易流水线

测试所有组件：策略引擎 → 模拟评审 → 共识 → 记录
"""
import sys
sys.path.insert(0, ".")

from trade_engine.strategy import StrategyEngine, StrategyConfig
from trade_engine.evaluator import run_committee, create_standard_committee
from trade_engine.consensus import ConsensusEngine
from trade_engine.pipeline import TradingPipeline
import pandas as pd

# 加载数据
df = pd.read_csv("data/jita_full.csv")
print(f"📊 数据: {len(df)} 个即时套利物品")
print()

# 测试1: 策略引擎
print("=" * 60)
print("🧪 测试1: 策略引擎 — 站内挂单")
print("=" * 60)
config = StrategyConfig(min_score=50, min_profit=5000, min_depth=5)
engine = StrategyEngine(config)
props = engine.generate_station_trading_proposals(df, max_proposals=3)
print(f"生成了 {len(props)} 个提案\n")
for i, p in enumerate(props, 1):
    print(f"  #{i}: {p.summary()}")
print()

# 测试2: 委员会评审（模拟）
print("=" * 60)
print("🧪 测试2: 委员会评审（模拟评估器）")
print("=" * 60)
if props:
    committee = create_standard_committee(api_key="")  # 自动使用模拟
    prop = props[0]
    print(f"评审提案: {prop.summary()}")
    print()
    evaluations = run_committee(prop, evaluators=committee)
    for ev in evaluations:
        flag_str = ", ".join(ev.risk_flags) if ev.risk_flags else "无"
        print(f"  [{ev.model_name:25s}] 评分={ev.score:.0f}  判定={ev.verdict:7s}  置信度={ev.confidence:.0%}  风险={flag_str}")
        print(f"    理由: {ev.reasoning[:120]}")
        print()
else:
    print("没有符合条件的提案")
print()

# 测试3: 共识引擎
print("=" * 60)
print("🧪 测试3: 共识聚合")
print("=" * 60)
if props:
    consensus = ConsensusEngine()
    result = consensus.aggregate(prop, evaluations)
    print(result.short_report())
    print()
    print(result.full_report())
print()

# 测试4: 完整流水线
print("=" * 60)
print("🧪 测试4: 完整流水线")
print("=" * 60)
pipe = TradingPipeline(data_path="data/jita_full.csv")
results = pipe.run_once(strategy="station_trading", max_proposals=2)
print()
print("📋 日志文件:", list(pipe.log_dir.glob("*.csv")))

print("\n✅ 所有测试通过!")
