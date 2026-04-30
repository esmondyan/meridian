"""
MockEvaluator — 模拟评估器

用于开发/测试场景，不需要 API key。
基于规则进行简单的评分，模拟不同模型的评审风格。
"""

import random
import re
from typing import Optional
from .proposal import TradeProposal, Evaluation


class MockEvaluator:
    """模拟评估器，基于规则逻辑做评审"""

    def __init__(self, name: str, system_prompt: str = ""):
        self._name = name
        self._system_prompt = system_prompt

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, proposal: TradeProposal, market_context: dict = None) -> Evaluation:
        """基于规则做模拟评估"""
        s = proposal  # shorthand

        # === 基础分（基于价差比和深度）===
        base_score = 50

        # 价差加分
        if s.spread_ratio >= 20:
            base_score += 20
        elif s.spread_ratio >= 10:
            base_score += 15
        elif s.spread_ratio >= 5:
            base_score += 8
        elif s.spread_ratio >= 2:
            base_score += 3

        # 深度加分
        if s.market_depth >= 100:
            base_score += 10
        elif s.market_depth >= 20:
            base_score += 6
        elif s.market_depth >= 5:
            base_score += 3

        # RoC 加分
        if s.estimated_roc >= 20:
            base_score += 10
        elif s.estimated_roc >= 10:
            base_score += 5
        elif s.estimated_roc >= 5:
            base_score += 2

        # 利润加分
        if s.estimated_profit >= 1_000_000:
            base_score += 10
        elif s.estimated_profit >= 100_000:
            base_score += 5
        elif s.estimated_profit >= 10_000:
            base_score += 2

        # 风险扣分
        risk_flags = []
        suggestions = []

        if s.market_depth < 5:
            risk_flags.append("深度不足")
            base_score -= 15
            suggestions.append(f"建议降低挂单量至 {max(1, s.volume // 2)} 件")
        elif s.market_depth < 20:
            risk_flags.append("深度偏浅")
            base_score -= 5

        if s.estimated_roc < 5:
            risk_flags.append("收益率偏低")
            base_score -= 10

        if s.estimated_profit < 10_000:
            risk_flags.append("绝对利润过低")
            base_score -= 10
            suggestions.append("建议找利润更高的标的")

        if s.spread_ratio < 3:
            risk_flags.append("价差比过小")
            base_score -= 8

        # 持仓周期风险
        if s.holding_period in ("天",):
            risk_flags.append("持仓周期长")
            base_score -= 3
            suggestions.append("监控价差变化，及时调整挂单")

        # 按角色调整
        role_bias = self._role_adjustment()
        score = max(0, min(100, base_score + role_bias))

        # 判定
        if score >= 75:
            verdict = "approve"
        elif score >= 50:
            verdict = "hold" if random.random() < 0.3 else "approve"
        else:
            verdict = "reject"

        confidence = min(0.9, max(0.3, score / 100 + random.uniform(-0.1, 0.1)))

        # 角色化理由
        reasoning = self._generate_reasoning(score, proposal, risk_flags)

        return Evaluation(
            model_name=self._name,
            score=round(score, 1),
            confidence=round(confidence, 2),
            verdict=verdict,
            reasoning=reasoning,
            risk_flags=risk_flags,
            suggestions=suggestions,
        )

    def _role_adjustment(self) -> int:
        """按角色风格加减分"""
        name = self._name.lower()
        if "fundamental" in name:
            return random.randint(-5, 5)  # 中性
        elif "trader" in name:
            return random.randint(3, 10)  # 激进，容易给高分
        elif "risk" in name:
            return random.randint(-10, -2)  # 保守，容易扣分
        elif "local" in name:
            return random.randint(-3, 3)  # 中性偏严格
        return 0

    def _generate_reasoning(self, score: float, p: TradeProposal, risks: list[str]) -> str:
        """生成角色化的理由"""
        name = self._name.lower()
        item = p.item_name
        roc = p.estimated_roc
        spread = p.spread_ratio
        profit = p.estimated_profit

        if "fundamental" in name:
            if score >= 75:
                return f"{item} 价差比 {spread:.1f}%，深度 {p.market_depth}，流动性良好，基本面支持进场。"
            elif score >= 50:
                return f"{item} 基本面尚可，但深度/价差处于临界区域，建议观望。"
            else:
                return f"{item} 基本面不支撑——{'/'.join(risks)}，建议放弃。"

        elif "trader" in name:
            if score >= 75:
                return f"RoC {roc:.1f}% 回报不错，价差 {spread:.1f}% 有竞争力，快速进出可行。"
            elif score >= 50:
                return f"收益空间有限（利润 {profit:,.0f} ISK），看看有没有更好的标的。"
            else:
                return "资金效率太低，pass。"

        elif "risk" in name:
            risks_str = ", ".join(risks) if risks else "未发现明显异常"
            if score >= 75:
                return f"风险可控，{'/'.join(risks) if risks else '各项指标正常'}，批准执行。"
            elif score >= 50:
                return f"存在一定风险: {risks_str}，建议缩小仓位。"
            else:
                return f"风险过高: {risks_str}，禁止执行。"

        else:  # local
            if score >= 75:
                return "规则检查通过，条件满足执行标准。"
            elif score >= 50:
                return f"部分条件不满足: {', '.join(risks)}，建议调整后重新提交。"
            else:
                return "规则检查不通过，拒绝执行。"
