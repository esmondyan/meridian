"""
TradeProposal — 交易提案的标准化数据结构

所有组件之间传递的唯一数据契约。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class MarketSnapshot:
    """交易时刻的市场快照"""
    item_name: str
    type_id: int
    best_buy: float          # 买盘最高价
    best_sell: float         # 卖盘最低价
    best_buy_vol: int        # 最优买价位剩余量
    best_sell_vol: int       # 最优卖价位剩余量
    total_buy_vol: int       # 买盘总挂单量
    total_sell_vol: int      # 卖盘总挂单量
    buy_order_count: int     # 买单数量
    sell_order_count: int    # 卖单数量
    snapshot_time: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class TradeProposal:
    """
    交易提案 — 策略引擎输出的"要不要干一票"

    由 Strategy Engine 生成 → 交给 Multi-Model Evaluator 评审
    """
    # === 交易基本信息 ===
    item_name: str
    type_id: int
    action: str              # "buy" | "sell"

    # === 价格与量 ===
    price: float             # 挂单价
    volume: int              # 挂单数量
    total_cost: float        # 总成本 = price * volume

    # === 预期收益 ===
    estimated_profit: float  # 预期利润（税后）
    estimated_roc: float     # 预期收益率（%）
    holding_period: str      # 预计持仓周期: "分钟" | "小时" | "天"

    # === 市场背景 ===
    best_sell: float         # 当前最优卖价（Buy 时参考）
    best_buy: float          # 当前最优买价（Sell 时参考）
    spread_ratio: float      # 当前价差比（%）
    market_depth: int        # 最优价位深度
    liquidity_score: float   # 流动性评分 (0-100)

    # === 策略分析 ===
    strategy_name: str       # 策略名: "station_trading" | "instant_arb" | "regional_arb"
    strategy_rationale: str  # 为什么选这个品

    # === 可选字段 ===
    best_sell_vol: int = 0   # 最优卖价位剩余量（用于评估）

    # === 元信息 ===
    proposal_id: str = field(default_factory=lambda: f"prop_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}")
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source_data: Optional[dict] = None  # 附带原始数据，给评估器用

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        """人类可读的一行摘要"""
        if self.action == "buy":
            return (
                f"🟢 BUY  {self.item_name:30s} "
                f"@{self.price:>10,.0f} ISK x {self.volume:>4}件 "
                f"| 预期利润 {self.estimated_profit:>10,.0f} ISK "
                f"(RoC {self.estimated_roc:.1f}%) "
                f"| 市场价差 {self.spread_ratio:.1f}% "
                f"| 深度 {self.market_depth:,}"
            )
        else:
            return (
                f"🔴 SELL {self.item_name:30s} "
                f"@{self.price:>10,.0f} ISK x {self.volume:>4}件 "
                f"| 预期利润 {self.estimated_profit:>10,.0f} ISK "
                f"(RoC {self.estimated_roc:.1f}%)"
            )


@dataclass
class Evaluation:
    """
    单个模型对提案的评估结果
    """
    model_name: str           # "deepseek" | "claude" | "gpt" | ...
    score: float             # 评分 0-100
    confidence: float        # 置信度 0-1
    verdict: str             # "approve" | "reject" | "hold"
    reasoning: str           # 理由
    risk_flags: list[str]    # 风险标记，如 ["深度不足", "近期波动大"]
    suggestions: list[str]   # 建议调整，如 ["降低挂单量至20件"]

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "score": self.score,
            "confidence": self.confidence,
            "verdict": self.verdict,
            "reasoning": self.reasoning,
            "risk_flags": self.risk_flags,
            "suggestions": self.suggestions,
        }


@dataclass
class ConsensusResult:
    """
    多模型共识结果
    """
    proposal_id: str
    evaluations: list[Evaluation]

    # 聚合指标
    avg_score: float         # 平均分
    std_dev: float           # 标准差（分歧度）
    min_score: float
    max_score: float
    net_verdict: str         # "execute" | "review" | "skip"

    # 分歧分析
    disagreements: list[str]     # 具体分歧点
    suggestions_merged: list[str]  # 合并后的建议

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "avg_score": self.avg_score,
            "std_dev": self.std_dev,
            "min_score": self.min_score,
            "max_score": self.max_score,
            "net_verdict": self.net_verdict,
            "disagreements": self.disagreements,
            "suggestions_merged": self.suggestions_merged,
            "evaluations": [e.to_dict() for e in self.evaluations],
        }

    def short_report(self) -> str:
        """一行的共识报告"""
        emoji = {"execute": "✅", "review": "⚠️", "skip": "❌"}
        return (
            f"{emoji.get(self.net_verdict, '❓')} 共识: {self.net_verdict.upper()} "
            f"| 均分 {self.avg_score:.0f} ± {self.std_dev:.1f} "
            f"(范围 {self.min_score:.0f}-{self.max_score:.0f}) "
            f"| 评审 {len(self.evaluations)} 模型 "
            f"| 分歧: {', '.join(self.disagreements[:2]) if self.disagreements else '无'}"
        )

    def full_report(self) -> str:
        """完整评审报告"""
        lines = [
            f"{'='*60}",
            f"📋 交易提案评审报告",
            f"提案: {self.proposal_id}",
            f"{'='*60}",
            f"",
            f"📊 共识结果: {self.net_verdict.upper()}",
            f"  平均评分: {self.avg_score:.1f} / 100",
            f"  分歧度: {self.std_dev:.1f} ({'低' if self.std_dev < 10 else '中' if self.std_dev < 20 else '高'})",
            f"  评分范围: {self.min_score:.0f} ~ {self.max_score:.0f}",
            f"",
        ]
        for i, ev in enumerate(self.evaluations, 1):
            flag_str = ", ".join(ev.risk_flags) if ev.risk_flags else "无"
            sug_str = ", ".join(ev.suggestions) if ev.suggestions else "无"
            lines.extend([
                f"--- 评审官 #{i}: {ev.model_name} ---",
                f"  评分: {ev.score:.0f}/100 | 置信度: {ev.confidence:.0%} | 判定: {ev.verdict}",
                f"  理由: {ev.reasoning[:200]}",
                f"  风险: {flag_str}",
                f"  建议: {sug_str}",
                f"",
            ])
        if self.disagreements:
            lines.append(f"⚠️ 主要分歧点:")
            for d in self.disagreements:
                lines.append(f"  - {d}")
        if self.suggestions_merged:
            lines.append(f"💡 综合建议:")
            for s in self.suggestions_merged:
                lines.append(f"  - {s}")
        return "\n".join(lines)
