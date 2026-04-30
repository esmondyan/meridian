"""
Backtest — 交易提案回测与验证系统

三种验证方式：
1. 纸面交易模拟：记录提案 → N小时后查市场 → 看涨跌
2. 历史价差回测：用 ESI history 检查历史价差稳定性
3. 模型准确率追踪：长期积累，看哪个模型最靠谱
"""

import json
import csv
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

import pandas as pd

from .proposal import TradeProposal, Evaluation, ConsensusResult


# ============================================================
# 纸面交易追踪器
# ============================================================

@dataclass
class PaperTradeRecord:
    """一条纸面交易记录"""
    proposal_id: str
    item_name: str
    type_id: int
    action: str
    buy_price: float
    sell_price_target: float
    volume: int
    estimated_profit: float
    estimated_roc: float

    # 记录时间
    created_at: str

    # 结果（N小时后更新）
    actual_best_buy: Optional[float] = None
    actual_best_sell: Optional[float] = None
    actual_spread_ratio: Optional[float] = None
    actual_buy_vol: Optional[int] = None
    actual_sell_vol: Optional[int] = None

    # 结论
    buy_filled: Optional[bool] = None   # 挂单是否会被成交
    sell_filled: Optional[bool] = None
    actual_profit: Optional[float] = None
    checked_at: Optional[str] = None

    @property
    def initial_spread(self) -> float:
        """记录时的价差（卖价-买价）"""
        return 0  # 会在创建时补

    def would_fill(self, initial_best_buy: float = None, initial_best_sell: float = None) -> str:
        """判断这笔交易是否可能成交（改进版）"""
        if self.actual_best_buy is None or self.actual_best_sell is None:
            return "⏳ 待验证"

        buy_initial = initial_best_buy or self.buy_price
        sell_initial = initial_best_sell or self.buy_price * 1.2  # fallback

        # 站内挂单定价逻辑：
        # 我们挂的买价 > 当时最优买价 → 我们是新最优买价，有竞争力
        # 验证时：如果最新买价 高于我们的买价，说明有人在更高价买，我们的单可能还没被吃掉
        #         如果最新买价 低于我们的买价，说明有人卖到了我们的价位（被成交了）

        # 场景A：我们挂买价 15,331 > 原来最优买价 15,300
        #   → 我们的单子是新的最优买价，被卖家看到
        #   → 如果有卖家愿意卖 @ 15,331，我们的单子被吃

        # 验证（简单模型）：
        # 买单：如果买价往我们方向移动了（best_buy 下降），说明有人以更低价格成交了
        # 卖单：如果卖价往我们方向移动了（best_sell 上升），说明有人以更高价格成交了

        buy_filled = self.actual_best_buy <= self.buy_price * 0.999  # 买价降了，有人卖了
        sell_filled = self.actual_best_sell >= self.sell_price_target  # 卖价涨了，有人买了

        if buy_filled and sell_filled:
            self.buy_filled = True
            self.sell_filled = True
            fill_price_buy = min(self.buy_price, self.actual_best_buy)
            fill_price_sell = max(self.sell_price_target, self.actual_best_sell)
            self.actual_profit = fill_price_sell - fill_price_buy - fill_price_sell * 0.033
            return "✅ 买卖均可能成交"
        elif buy_filled:
            self.buy_filled = True
            return "🟡 买已成交，卖未成交（持仓中）"
        elif sell_filled:
            self.buy_filled = False
            self.sell_filled = True
            return "🟡 卖已触发（做空），待补货"
        else:
            self.buy_filled = False
            self.sell_filled = False
            return "❌ 价格未向有利方向变动"

    def summary_line(self) -> str:
        status = self.would_fill() if self.actual_best_buy else "⏳ 待验证"
        return (f"{status:30s} | {self.item_name:35s} "
                f"买入 {self.buy_price:>10,.0f} ISK | "
                f"目标卖 {self.sell_price_target:>10,.0f} ISK | "
                f"利润 {self.estimated_profit:>10,.0f} ISK | "
                f"RoC {self.estimated_roc:>6.1f}%")


class PaperTrader:
    """
    纸面交易模拟器

    记录所有提案 → 定时检查市场 → 跟踪准确率
    """

    def __init__(self, log_dir: str = None):
        self.log_dir = Path(log_dir or "data/papertrades")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._records: list[PaperTradeRecord] = []
        self._load()

    def _path(self) -> Path:
        return self.log_dir / "trades.csv"

    def _load(self):
        """加载历史记录"""
        path = self._path()
        if path.exists():
            df = pd.read_csv(path)
            for _, row in df.iterrows():
                self._records.append(PaperTradeRecord(
                    proposal_id=row["proposal_id"],
                    item_name=row["item_name"],
                    type_id=int(row["type_id"]),
                    action=row["action"],
                    buy_price=float(row["buy_price"]),
                    sell_price_target=float(row["sell_price_target"]),
                    volume=int(row["volume"]),
                    estimated_profit=float(row["estimated_profit"]),
                    estimated_roc=float(row["estimated_roc"]),
                    created_at=row["created_at"],
                    actual_best_buy=row.get("actual_best_buy"),
                    actual_best_sell=row.get("actual_best_sell"),
                    actual_spread_ratio=row.get("actual_spread_ratio"),
                    actual_buy_vol=row.get("actual_buy_vol"),
                    actual_sell_vol=row.get("actual_sell_vol"),
                    buy_filled=row.get("buy_filled"),
                    sell_filled=row.get("sell_filled"),
                    actual_profit=row.get("actual_profit"),
                    checked_at=row.get("checked_at"),
                ))

    def _save(self):
        """保存到 CSV"""
        rows = []
        for r in self._records:
            rows.append({
                "proposal_id": r.proposal_id,
                "item_name": r.item_name,
                "type_id": r.type_id,
                "action": r.action,
                "buy_price": r.buy_price,
                "sell_price_target": r.sell_price_target,
                "volume": r.volume,
                "estimated_profit": r.estimated_profit,
                "estimated_roc": r.estimated_roc,
                "created_at": r.created_at,
                "actual_best_buy": r.actual_best_buy or "",
                "actual_best_sell": r.actual_best_sell or "",
                "actual_spread_ratio": r.actual_spread_ratio or "",
                "actual_buy_vol": r.actual_buy_vol or "",
                "actual_sell_vol": r.actual_sell_vol or "",
                "buy_filled": r.buy_filled or "",
                "sell_filled": r.sell_filled or "",
                "actual_profit": r.actual_profit or "",
                "checked_at": r.checked_at or "",
            })
        pd.DataFrame(rows).to_csv(self._path(), index=False)

    def record(self, proposal: TradeProposal):
        """记录一条新交易提案"""
        record = PaperTradeRecord(
            proposal_id=proposal.proposal_id,
            item_name=proposal.item_name,
            type_id=proposal.type_id,
            action=proposal.action,
            buy_price=proposal.price,
            sell_price_target=proposal.best_buy,  # 目标卖出价 = 当时的最优买价
            volume=proposal.volume,
            estimated_profit=proposal.estimated_profit,
            estimated_roc=proposal.estimated_roc,
            created_at=proposal.created_at,
        )
        self._records.append(record)
        self._save()
        return record

    def check(self, type_id: int, best_buy: float, best_sell: float,
              buy_vol: int = 0, sell_vol: int = 0):
        """检查某个物品的纸面交易是否成交"""
        now = datetime.now().isoformat()
        for r in self._records:
            if r.type_id == type_id and r.checked_at is None:
                r.actual_best_buy = best_buy
                r.actual_best_sell = best_sell
                r.actual_spread_ratio = (best_sell - best_buy) / best_sell * 100 if best_sell > 0 else 0
                r.actual_buy_vol = buy_vol
                r.actual_sell_vol = sell_vol
                r.checked_at = now
                r.would_fill()
        self._save()

    def check_all(self, market_df: pd.DataFrame):
        """用最新市场数据检查所有待验证的交易"""
        matched = 0
        for r in self._records:
            if r.checked_at:
                continue
            row = market_df[market_df["type_id"] == r.type_id]
            if row.empty:
                continue
            self.check(
                type_id=r.type_id,
                best_buy=float(row.iloc[0]["best_buy"]),
                best_sell=float(row.iloc[0]["best_sell"]),
                buy_vol=int(row.iloc[0].get("best_buy_vol", 0)),
                sell_vol=int(row.iloc[0].get("best_sell_vol", 0)),
            )
            matched += 1
        return matched

    @property
    def pending(self) -> list[PaperTradeRecord]:
        """待验证的交易"""
        return [r for r in self._records if r.checked_at is None]

    @property
    def completed(self) -> list[PaperTradeRecord]:
        """已验证的交易"""
        return [r for r in self._records if r.checked_at is not None]

    def accuracy_report(self) -> dict:
        """准确率报告"""
        done = self.completed
        if not done:
            return {"total": 0, "filled": 0, "missed": 0, "rate": 0}

        filled = sum(1 for r in done if r.buy_filled)
        return {
            "total": len(done),
            "filled": filled,
            "missed": len(done) - filled,
            "fill_rate": 0,
        }
        if done:
            filled = sum(1 for r in done if r.buy_filled)
            result["filled"] = filled
            result["missed"] = len(done) - filled
            result["fill_rate"] = filled / len(done) * 100
        return result

    def summary(self) -> str:
        """人类可读的摘要"""
        pending = len(self.pending)
        done = self.completed
        acc = self.accuracy_report()

        lines = [
            f"📊 纸面交易追踪器",
            f"  总记录: {len(self._records)} 笔",
            f"  待验证: {pending} 笔",
            f"  已验证: {acc['total']} 笔 (成交 {acc['filled']}, 未成交 {acc['missed']})",
            f"  成交率: {acc['fill_rate']:.1f}%",
        ]

        if self._records:
            lines.append(f"\n  最新交易:")
            for r in self._records[-5:]:
                lines.append(f"    {r.summary_line()}")

        return "\n".join(lines)


# ============================================================
# 模型准确率追踪
# ============================================================

class AccuracyTracker:
    """
    追踪每个模型的评审准确率

    当纸面交易结果出来后，对比模型的评审意见：
    - 模型说 approve → 实际 fill → 对
    - 模型说 reject  → 实际 no fill → 对
    - 其他情况 → 错
    """

    def __init__(self, log_dir: str = None):
        self.log_dir = Path(log_dir or "data/accuracy")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.log_dir / "model_accuracy.csv"
        self._records: list[dict] = []
        self._load()

    def _load(self):
        if self._path.exists():
            df = pd.read_csv(self._path)
            self._records = df.to_dict("records")

    def _save(self):
        pd.DataFrame(self._records).to_csv(self._path, index=False)

    def record_evaluation(self, proposal: TradeProposal, evaluation: Evaluation,
                          actual_filled: bool, actual_profit: float):
        """记录一次评审的准确性"""
        # 判断是否准确
        if evaluation.verdict == "approve" and actual_filled:
            correct = True
        elif evaluation.verdict == "reject" and not actual_filled:
            correct = True
        else:
            correct = False

        self._records.append({
            "timestamp": datetime.now().isoformat(),
            "proposal_id": proposal.proposal_id,
            "item_name": proposal.item_name,
            "model_name": evaluation.model_name,
            "verdict": evaluation.verdict,
            "score": evaluation.score,
            "actual_filled": actual_filled,
            "actual_profit": actual_profit,
            "correct": correct,
        })
        self._save()

    def model_stats(self) -> pd.DataFrame:
        """每个模型的准确率统计"""
        if not self._records:
            return pd.DataFrame()

        df = pd.DataFrame(self._records)
        stats = df.groupby("model_name").agg(
            total=("correct", "count"),
            correct=("correct", "sum"),
            avg_score=("score", "mean"),
        )
        stats["accuracy"] = (stats["correct"] / stats["total"] * 100).round(1)
        return stats.reset_index()

    def summary(self) -> str:
        df = self.model_stats()
        if df.empty:
            return "暂无准确率数据"

        lines = ["📊 模型准确率追踪"]
        for _, r in df.iterrows():
            emoji = "🟢" if r["accuracy"] >= 70 else "🟡" if r["accuracy"] >= 50 else "🔴"
            lines.append(
                f"  {emoji} {r['model_name']:30s} "
                f"准确率 {r['accuracy']:>5.1f}% "
                f"({r['correct']:.0f}/{r['total']:.0f}) "
                f"平均评分 {r['avg_score']:.1f}"
            )
        return "\n".join(lines)


# ============================================================
# 历史价差稳定性分析
# ============================================================

def analyze_spread_stability(
    market_df: pd.DataFrame,
    top_n: int = 20,
) -> pd.DataFrame:
    """
    分析站内挂单标的的价差稳定性

    用当前订单数据推断价差可靠性：
    - 订单数量多 → 价差更稳定（竞争充分）
    - 深度大 → 价差更可靠
    - 买卖单数量平衡 → 价差更真实
    """
    df = market_df[market_df["bid_ask_spread"] > 0].copy()

    # 稳定性评分因子
    df["order_balance"] = abs(df["buy_order_count"] - df["sell_order_count"]) / (df["buy_order_count"] + df["sell_order_count"] + 1)
    balance_score = (1 - df["order_balance"]) * 30  # 平衡度越高得分越高

    df["depth_total"] = df["total_buy_vol"] + df["total_sell_vol"]
    depth_score = df["depth_total"].rank(pct=True) * 30

    df["spread_score"] = df["spread_ratio"].rank(pct=True) * 40

    df["stability_score"] = (balance_score + depth_score + df["spread_score"]).round(1)

    # 综合评分（价差稳定性 + 原始站内评分）取平均
    df["final_score"] = ((df["stability_score"] + df["station_score"]) / 2).round(1)

    return df.sort_values("final_score", ascending=False).head(top_n)[
        ["name", "best_buy", "best_sell", "spread_ratio", "station_score",
         "stability_score", "final_score", "buy_order_count", "sell_order_count",
         "total_buy_vol", "total_sell_vol"]
    ]
