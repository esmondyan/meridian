"""
Pipeline — 交易流水线

完整的端到端流程：采集 → 评分 → 策略 → 评审 → 执行 → 记录
"""

import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd

from .proposal import TradeProposal, Evaluation, ConsensusResult
from .evaluator import run_committee, create_standard_committee
from .consensus import ConsensusEngine
from .strategy import StrategyEngine, StrategyConfig


class TradingPipeline:
    """
    交易流水线主控

    用法:
        pipe = TradingPipeline(data_dir="path/to/data.csv")
        result = pipe.run_once()
    """

    def __init__(
        self,
        data_path: str = None,
        strategy_config: StrategyConfig = None,
        consensus_config: dict = None,
        api_key: str = None,
        log_dir: str = None,
    ):
        self.data_path = data_path or "data/jita_full.csv"
        self.api_key = api_key
        self.log_dir = Path(log_dir or "data/logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.strategy = StrategyEngine(strategy_config or StrategyConfig())
        self.consensus = ConsensusEngine(**(consensus_config or {}))
        self.evaluators = None  # 懒加载

    def _load_market_data(self) -> pd.DataFrame:
        """加载市场数据 — 优先用站内挂单数据（有正常市场价差）"""
        station_path = Path(self.data_path).parent / "station_jita_full.csv"
        if station_path.exists():
            df = pd.read_csv(station_path)
            print(f"  使用站内挂单数据 ({len(df)} 物品)")
        else:
            df = pd.read_csv(self.data_path)
            print(f"  使用即时套利数据 ({len(df)} 物品)")

        for col in ["best_buy", "best_sell", "bid_ask_spread", "spread_ratio",
                     "best_buy_vol", "best_sell_vol", "station_score",
                     "total_buy_vol", "total_sell_vol", "gross_margin"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        return df

    def _init_evaluators(self):
        """初始化评估委员会（懒加载）"""
        if self.evaluators is None:
            self.evaluators = create_standard_committee(self.api_key)

    def _log_decision(self, proposal: TradeProposal, consensus: ConsensusResult):
        """记录决策到 CSV 日志"""
        path = self.log_dir / "trade_log.csv"
        exists = path.exists()

        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow([
                    "timestamp", "proposal_id", "item_name", "action",
                    "price", "volume", "total_cost", "estimated_profit",
                    "estimated_roc", "strategy", "avg_score", "std_dev",
                    "net_verdict", "num_evaluators",
                ])
            writer.writerow([
                datetime.now().isoformat(),
                proposal.proposal_id,
                proposal.item_name,
                proposal.action,
                proposal.price,
                proposal.volume,
                proposal.total_cost,
                proposal.estimated_profit,
                proposal.estimated_roc,
                proposal.strategy_name,
                consensus.avg_score,
                consensus.std_dev,
                consensus.net_verdict,
                len(consensus.evaluations),
            ])

    def run_once(
        self,
        strategy: str = "station_trading",
        max_proposals: int = 3,
    ) -> list[dict]:
        """
        跑一轮流水线

        Args:
            strategy: "station_trading" | "instant_arb"
            max_proposals: 生成多少个提案候选

        Returns:
            list[dict] — 每个提案的完整评审报告
        """
        t0 = datetime.now()
        self._init_evaluators()

        print(f"[{t0:%H:%M:%S}] 📊 加载市场数据...")
        market_df = self._load_market_data()
        print(f"  共 {len(market_df)} 个物品")

        # === 步骤 1: 策略引擎生成提案 ===
        print(f"[{datetime.now():%H:%M:%S}] 🧠 策略引擎生成提案 ({strategy})...")
        if strategy == "instant_arb":
            proposals = self.strategy.generate_instant_arb_proposals(market_df, max_proposals)
        else:
            proposals = self.strategy.generate_station_trading_proposals(market_df, max_proposals)

        print(f"  生成了 {len(proposals)} 个提案")

        # === 步骤 2-3: 委员会评审 + 共识 ===
        results = []
        for i, prop in enumerate(proposals, 1):
            print(f"\n[{datetime.now():%H:%M:%S}] 🔍 评审提案 #{i}...")
            print(f"  {prop.summary()}")

            # 评审
            evaluations = run_committee(prop, api_key=self.api_key, evaluators=self.evaluators)
            print(f"  收到 {len(evaluations)} 个模型评审")

            # 共识
            consensus = self.consensus.aggregate(prop, evaluations)
            print(f"  {consensus.short_report()}")

            # 记录
            self._log_decision(prop, consensus)
            if consensus.net_verdict == "execute":
                print(f"  ✅ 执行! ({consensus.full_report()[:100]}...)")

            results.append({
                "proposal": prop,
                "evaluations": evaluations,
                "consensus": consensus,
                "report": consensus.full_report(),
            })

        elapsed = (datetime.now() - t0).total_seconds()
        print(f"\n[{datetime.now():%H:%M:%S}] ✅ 流水线完成，耗时 {elapsed:.1f} 秒")
        print(f"  共评审 {len(proposals)} 个提案")
        print(f"  执行: {sum(1 for r in results if r['consensus'].net_verdict == 'execute')}")
        print(f"  复核: {sum(1 for r in results if r['consensus'].net_verdict == 'review')}")
        print(f"  跳过: {sum(1 for r in results if r['consensus'].net_verdict == 'skip')}")

        return results


def run_pipeline(
    data_path: str = None,
    strategy: str = "station_trading",
    api_key: str = None,
    show_full_report: bool = False,
) -> list[dict]:
    """快捷函数：一行运行流水线"""
    pipe = TradingPipeline(data_path=data_path, api_key=api_key)
    results = pipe.run_once(strategy=strategy)

    for r in results:
        if show_full_report or r["consensus"].net_verdict == "execute":
            print(r["report"])

    return results
