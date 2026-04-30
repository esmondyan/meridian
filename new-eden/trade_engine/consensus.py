"""
Consensus Engine — 共识聚合引擎

将多个模型的评审结果聚合为最终决策。
"""

import statistics
from typing import Optional
from .proposal import TradeProposal, Evaluation, ConsensusResult


class ConsensusEngine:
    """
    共识引擎配置
    """

    def __init__(
        self,
        execute_threshold: float = 65.0,    # 均分 >= 此值 → execute
        skip_threshold: float = 40.0,        # 均分 < 此值 → skip
        max_disagreement: float = 20.0,      # 标准差 <= 此值 → 低分歧
        min_evaluators: int = 2,             # 最少需要几个评审
        veto_models: list[str] = None,       # 哪些模型有一票否决权
    ):
        self.execute_threshold = execute_threshold
        self.skip_threshold = skip_threshold
        self.max_disagreement = max_disagreement
        self.min_evaluators = min_evaluators
        self.veto_models = veto_models or ["deepseek-risk"]

    def aggregate(self, proposal: TradeProposal, evaluations: list[Evaluation]) -> ConsensusResult:
        """聚合多模型评审结果"""
        if len(evaluations) < self.min_evaluators:
            return ConsensusResult(
                proposal_id=proposal.proposal_id,
                evaluations=evaluations,
                avg_score=0,
                std_dev=0,
                min_score=0,
                max_score=0,
                net_verdict="review",
                disagreements=["评审数不足"],
                suggestions_merged=["等待更多模型评审"],
            )

        scores = [e.score for e in evaluations]
        avg_score = statistics.mean(scores)
        std_dev = statistics.stdev(scores) if len(scores) > 1 else 0
        min_score = min(scores)
        max_score = max(scores)

        # === 分歧分析 ===
        disagreements = []
        for ev in evaluations:
            if ev.risk_flags:
                disagreements.extend(ev.risk_flags)

        # 找模型之间的分歧点
        approvals = [e for e in evaluations if e.verdict == "approve"]
        rejections = [e for e in evaluations if e.verdict == "reject"]
        holds = [e for e in evaluations if e.verdict == "hold"]

        if approvals and rejections:
            disagreements.append(
                f"{'、'.join(e.model_name for e in approvals)} 支持, "
                f"{'、'.join(e.model_name for e in rejections)} 反对"
            )

        # === Veto 检查 ===
        veto_active = any(
            e.verdict == "reject" and e.model_name in self.veto_models
            for e in evaluations
        )

        # === 最终决策 ===
        if veto_active:
            net_verdict = "skip"
        elif avg_score >= self.execute_threshold and std_dev <= self.max_disagreement:
            net_verdict = "execute"
        elif avg_score >= self.execute_threshold and std_dev > self.max_disagreement:
            net_verdict = "review"  # 高分但分歧大 → 人工审核
        elif avg_score > self.skip_threshold:
            net_verdict = "review"
        else:
            net_verdict = "skip"

        # === 合并建议 ===
        merged_suggestions = []
        seen = set()
        for ev in evaluations:
            for sug in ev.suggestions:
                key = sug.lower().strip()
                if key not in seen:
                    merged_suggestions.append(sug)
                    seen.add(key)

        # 去重风险标记
        seen_risks = set()
        unique_disagreements = []
        for d in disagreements:
            if d not in seen_risks:
                unique_disagreements.append(d)
                seen_risks.add(d)

        return ConsensusResult(
            proposal_id=proposal.proposal_id,
            evaluations=evaluations,
            avg_score=round(avg_score, 1),
            std_dev=round(std_dev, 2),
            min_score=round(min_score, 1),
            max_score=round(max_score, 1),
            net_verdict=net_verdict,
            disagreements=unique_disagreements,
            suggestions_merged=merged_suggestions,
        )
