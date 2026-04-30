"""
Strategy Engine — 策略引擎

从评分结果中选出最优候选交易，生成 TradeProposal。
支持多种策略模式。
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd

from .proposal import TradeProposal, MarketSnapshot


@dataclass
class StrategyConfig:
    """策略配置"""
    min_score: float = 60.0          # 最低评分才考虑
    min_profit: float = 10_000.0     # 最低利润 (ISK)
    max_capital_per_trade: float = 100_000_000.0  # 单笔最大投入
    min_depth: int = 5               # 最低深度
    min_spread_ratio: float = 3.0    # 最低价差比 (%)
    max_position_count: int = 3      # 同时持仓数


class StrategyEngine:
    """
    策略引擎 — 根据评分数据和市场快照生成交易提案
    """

    def __init__(self, config: StrategyConfig = None):
        self.config = config or StrategyConfig()

    def generate_station_trading_proposals(
        self, market_df: pd.DataFrame, max_proposals: int = 5
    ) -> list[TradeProposal]:
        """
        站内挂单策略 — 找最佳挂单套利标的

        market_df: 从采集器得到的数据（含评分、价差、深度等）
        兼容两种数据格式：站内挂单数据 (station_score/bid_ask_spread) 和即时套利数据 (eve_score/spread)
        """
        props = []

        # 检测列名格式
        score_col = "station_score" if "station_score" in market_df.columns else "eve_score"
        spread_col = "bid_ask_spread" if "bid_ask_spread" in market_df.columns else "spread"
        profit_col = "gross_margin" if "gross_margin" in market_df.columns else "profit_after_tax"

        # 筛选备选池
        candidates = market_df[
            (market_df[score_col] >= self.config.min_score)
            & (market_df["best_buy_vol"] >= self.config.min_depth)
            & (market_df["best_sell_vol"] >= self.config.min_depth)
            & (market_df[spread_col] > 0)  # 正常市场：卖价 > 买价
        ]

        # 按评分排序，取前 N
        if profit_col in market_df.columns:
            candidates = candidates[candidates[profit_col] >= self.config.min_profit]
        candidates = candidates.sort_values(score_col, ascending=False)

        for _, row in candidates.head(max_proposals).iterrows():
            # 正确的站内挂单定价:
            # 买单: 比当前最高买价略高一点（和现有买家竞争，出价更优）
            # 卖单: 比当前最低卖价略低一点（和现有卖家竞争，要价更低）
            buy_price = round(row["best_buy"] * 1.002, 0)  # 比最优买价高 0.2%
            sell_price = round(row["best_sell"] * 0.998, 0)  # 比最优卖价低 0.2%

            # 确保买入价 < 卖出价（否则没利润空间）
            if buy_price >= sell_price:
                continue

            # 真实毛利率 = 卖出价 - 买入价 - 税费
            net_profit_per_unit = sell_price - buy_price - sell_price * 0.033
            if net_profit_per_unit <= 0:
                continue

            # 挂单量 = 最优质价位深度的 1/4（保守）
            volume = max(1, min(row["best_buy_vol"], row["best_sell_vol"]) // 4)
            volume = min(volume, 200)

            # 预期利润 = 每件利润 × 挂单量
            total_net_profit = net_profit_per_unit * volume

            roc = (net_profit_per_unit / buy_price * 100) if buy_price > 0 else 0

            # 成本检查
            total_cost = buy_price * volume
            if total_cost > self.config.max_capital_per_trade:
                # 按资金上限调整
                volume = int(self.config.max_capital_per_trade / buy_price)
                total_cost = buy_price * volume

            market_snapshot = {
                "total_buy_vol": int(row.get("total_buy_vol", 0)),
                "total_sell_vol": int(row.get("total_sell_vol", 0)),
                "buy_order_count": int(row.get("buy_order_count", 0)),
                "sell_order_count": int(row.get("sell_order_count", 0)),
                "best_sell_vol": int(row.get("best_sell_vol", 0)),
                "best_buy_vol": int(row.get("best_buy_vol", 0)),
            }

            prop = TradeProposal(
                item_name=str(row.get("name", f"type_{row.get('type_id', 0)}")),
                type_id=int(row.get("type_id", 0)),
                action="buy",
                price=buy_price,
                volume=volume,
                total_cost=total_cost,
                estimated_profit=round(total_net_profit, 0),
                estimated_roc=round(roc, 2),
                holding_period="小时",
                best_sell=float(row["best_sell"]),
                best_buy=float(row["best_buy"]),
                spread_ratio=float(row["spread_ratio"]),
                market_depth=min(int(row.get("best_buy_vol", 0)), int(row.get("best_sell_vol", 0))),
                best_sell_vol=int(row.get("best_sell_vol", 0)),
                liquidity_score=float(row.get(score_col, 50)),
                strategy_name="station_trading",
                strategy_rationale=(
                    f"{row.get('name', '?')} 价差比 {row['spread_ratio']:.1f}%，"
                    f"深度 {int(row.get('best_buy_vol', 0))}/{int(row.get('best_sell_vol', 0))} 件，"
                    f"RoC {roc:.1f}%。"
                    f"挂买入单 @ {buy_price:,.0f}，预期成交后挂 @ {sell_price:,.0f} 卖出。"
                ),
                source_data=market_snapshot,
            )
            props.append(prop)

        return props

    def generate_instant_arb_proposals(
        self, market_df: pd.DataFrame, max_proposals: int = 5
    ) -> list[TradeProposal]:
        """
        即时套利策略 — 找买单 > 卖价的漏洞

        这种机会极少，但如果有就是零风险套利。
        """
        props = []

        arb = market_df[market_df["spread"] > 0].sort_values("profit_after_tax", ascending=False)

        for _, row in arb.head(max_proposals).iterrows():
            best_buy = float(row["best_buy"])
            best_sell = float(row["best_sell"])
            net_profit = best_buy - best_sell - best_buy * 0.033

            if net_profit <= 0:
                continue

            # 能买多少件？
            volume = min(
                int(row.get("best_sell_vol", 0)),
                int(row.get("best_buy_vol", 0)),
                50,  # 保守，不超过 50 件
            )
            if volume <= 0:
                continue

            total_cost = best_sell * volume
            if total_cost > self.config.max_capital_per_trade:
                volume = int(self.config.max_capital_per_trade / best_sell)
                total_cost = best_sell * volume

            total_profit = net_profit * volume
            roc = (net_profit / best_sell * 100) if best_sell > 0 else 0

            market_snapshot = {
                "total_buy_vol": int(row.get("total_buy_vol", 0)),
                "total_sell_vol": int(row.get("total_sell_vol", 0)),
                "best_sell_vol": int(row.get("best_sell_vol", 0)),
                "best_buy_vol": int(row.get("best_buy_vol", 0)),
            }

            prop = TradeProposal(
                item_name=str(row.get("name", f"type_{row.get('type_id', 0)}")),
                type_id=int(row.get("type_id", 0)),
                action="buy",
                price=best_sell,
                volume=volume,
                total_cost=total_cost,
                estimated_profit=round(total_profit, 0),
                estimated_roc=round(roc, 2),
                holding_period="分钟",
                best_sell=best_sell,
                best_buy=best_buy,
                spread_ratio=float(row["spread_ratio"]),
                market_depth=volume,
                liquidity_score=float(row.get("eve_score", 50)),
                strategy_name="instant_arb",
                strategy_rationale=(
                    f"即时套利! 买单 {best_buy:,.0f} > 卖价 {best_sell:,.0f}，"
                    f"单件利润 {net_profit:,.0f} ISK (税后)，共 {volume} 件。"
                ),
                source_data=market_snapshot,
            )
            props.append(prop)

        return props
