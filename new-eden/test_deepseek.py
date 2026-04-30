"""
测试真实 DeepSeek API 评审
"""
import sys, os
sys.path.insert(0, ".")

api_key = os.environ.get("DEEPSEEK_API_KEY", "")
print(f"DEEPSEEK_API_KEY: {'已设置' if api_key else '未设置'}")

from trade_engine.proposal import TradeProposal
from trade_engine.evaluator import run_committee, create_standard_committee

# 创建一个测试提案
prop = TradeProposal(
    item_name="Station Vault Container",
    type_id=36152,
    action="buy",
    price=145500.0,
    volume=8,
    total_cost=1164000.0,
    estimated_profit=892095.0,
    estimated_roc=153.3,
    holding_period="小时",
    best_sell=150000.0,
    best_buy=370000.0,
    spread_ratio=146.7,
    market_depth=26,
    liquidity_score=78.0,
    strategy_name="station_trading",
    strategy_rationale="Station Vault Container 价差比 146.7%，深度 26 件，RoC 153.3%。"
)

if api_key:
    print("\n✅ 使用真实 DeepSeek API 评审...")
    committee = create_standard_committee(api_key)
    evaluations = run_committee(prop, evaluators=committee)

    for ev in evaluations:
        print(f"\n{'='*60}")
        print(f"🤖 [{ev.model_name}]")
        print(f"  评分: {ev.score:.0f}/100  置信度: {ev.confidence:.0%}  判定: {ev.verdict}")
        print(f"  理由: {ev.reasoning}")
        print(f"  风险: {', '.join(ev.risk_flags) if ev.risk_flags else '无'}")
        print(f"  建议: {', '.join(ev.suggestions) if ev.suggestions else '无'}")
else:
    print("❌ 未设置 API key，跳过真实测试")
