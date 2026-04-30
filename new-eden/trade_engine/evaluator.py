"""
Multi-Model Evaluator — 多模型交易评审委员会

负责调用不同 LLM 对交易提案进行独立评估。
每个模型从不同视角评审同一个提案，独立给出评分和意见。
"""

import json
import time
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Any

from .proposal import TradeProposal, Evaluation

# ============================================================
# 模型评估器接口
# ============================================================

class ModelEvaluator(ABC):
    """所有模型评估器的抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """模型名称标识"""
        ...

    @abstractmethod
    def evaluate(self, proposal: TradeProposal, market_context: dict) -> Evaluation:
        """评估交易提案，返回评审结果"""
        ...


# ============================================================
# System prompts — 每个模型/角色的专属视角
# ============================================================

# --- 基本面分析师（DeepSeek Claude 风格）---
SYSTEM_FUNDAMENTAL = """你是一个 EVE Online 市场基本面分析师。你擅长：
- 分析价差结构和订单簿深度
- 评估物品流动性（挂单量、成交频率）
- 判断价格是否处于合理区间
- 考虑物品类别、市场周期因素

评审要求：
1. 给出 0-100 的评分（低于 50 表示不应执行）
2. 给出置信度 0-1
3. 判定：approve / reject / hold
4. 列出风险点和建议

你的特点是：保守、重数据、看重流动性。"""

# --- 趋势交易员（GPT 风格）---
SYSTEM_TRADER = """你是一个 EVE Online 趋势交易员。你擅长：
- 判断当下是否是进场好时机
- 评估价差比的竞争性（其他交易员会不会抢单）
- 分析买卖单数量变化趋势
- 计算 RoC 和资金周转效率

评审要求：
1. 给出 0-100 的评分
2. 给出置信度 0-1
3. 判定：approve / reject / hold
4. 列出风险点和建议

你的特点是：激进、重收益、快进快出。"""

# --- 风控官（DeepSeek 风格）---
SYSTEM_RISK = """你是一个 EVE Online 交易风控官。你擅长：
- 评估单笔交易的风险敞口
- 分析市场操控风险（深层挂单的稳定性）
- 评估价差缩小风险、持仓周期风险
- 检查是否过度集中某一品类

评审要求：
1. 给出 0-100 的评分
2. 给出置信度 0-1
3. 判定：approve / reject / hold
4. 列出风险点和建议

你的特点是：极度保守、宁可错过不犯错、重仓位管理。"""

# --- 本地智能（轻量级快速评审）---
SYSTEM_LOCAL = """你是一个 EVE Online 交易辅助评估器。你擅长：
- 快速对比相似历史交易
- 检查条件是否满足预设规则集
- 做最后的"合理性检查"

评审要求：
1. 给出 0-100 的评分
2. 给出置信度 0-1
3. 判定：approve / reject / hold
4. 列出风险点和建议

你的特点是：直白、规则驱动、不废话。"""


# ============================================================
# 用户评审 prompt 模板
# ============================================================

USER_EVALUATE_TEMPLATE = """请评审以下 EVE Online 交易提案：

## 交易信息
- 物品: {item_name} (type_id: {type_id})
- 动作: {action} (@ {price} ISK x {volume} 件)
- 总成本: {total_cost:,.0f} ISK
- 策略: {strategy_name}

## 预期收益
- 税后利润: {profit:,.0f} ISK
- RoC: {roc:.1f}%
- 预计持仓: {holding_period}

## 市场快照
- 最优卖价: {best_sell:,.0f} ISK
- 最优买价: {best_buy:,.0f} ISK
- 当前价差比: {spread_ratio:.1f}%
- 最优买价位深度: {buy_depth:,} 件
- 最优卖价位深度: {sell_depth:,} 件
- 买盘总挂单: {total_buy:,}
- 卖盘总挂单: {total_sell:,}
- 买单数量: {buy_count}
- 卖单数量: {sell_count}

## 策略理由
{rationale}

请从你的专业视角评审此交易提案。
严格按照以下 JSON 格式回复（仅 JSON，不要额外文字）：
```json
{{
  "score": <0-100>,
  "confidence": <0.0-1.0>,
  "verdict": "<approve|reject|hold>",
  "reasoning": "<理由，2-3句话>",
  "risk_flags": ["<风险标签>"],
  "suggestions": ["<建议>"]
}}
```"""


# ============================================================
# DeepSeek Evaluator（通过 DeepSeek API）
# ============================================================

class DeepSeekEvaluator(ModelEvaluator):
    """通过 DeepSeek API 调用，支持注入不同的 system prompt 改变角色"""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        temperature: float = 0.3,
    ):
        self._name = name
        self._system_prompt = system_prompt
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature

    @property
    def name(self) -> str:
        return self._name

    def _build_user_prompt(self, proposal: TradeProposal) -> str:
        return USER_EVALUATE_TEMPLATE.format(
            item_name=proposal.item_name,
            type_id=proposal.type_id,
            action=proposal.action.upper(),
            price=proposal.price,
            volume=proposal.volume,
            total_cost=proposal.total_cost,
            strategy_name=proposal.strategy_name,
            profit=proposal.estimated_profit,
            roc=proposal.estimated_roc,
            holding_period=proposal.holding_period,
            best_sell=proposal.best_sell,
            best_buy=proposal.best_buy,
            spread_ratio=proposal.spread_ratio,
            buy_depth=proposal.market_depth,
            sell_depth=proposal.best_sell_vol,  # 最优卖价位深度
            total_buy=proposal.source_data.get("total_buy_vol", 0) if proposal.source_data else 0,
            total_sell=proposal.source_data.get("total_sell_vol", 0) if proposal.source_data else 0,
            buy_count=proposal.source_data.get("buy_order_count", 0) if proposal.source_data else 0,
            sell_count=proposal.source_data.get("sell_order_count", 0) if proposal.source_data else 0,
            rationale=proposal.strategy_rationale,
        )

    def _parse_response(self, text: str) -> dict:
        """从 LLM 回复中提取 JSON"""
        # 尝试提取 ```json ... ``` 块
        m = re.search(r'```(?:json)?\s*({.*?})\s*```', text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        # 直接解析
        return json.loads(text)

    def _call_api(self, prompt: str) -> str:
        """调用 DeepSeek API"""
        import urllib.request

        payload = json.dumps({
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self._temperature,
            "max_tokens": 1024,
        }).encode()

        req = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            # API 调用失败时，返回一个"跳过"评审
            error_msg = str(e)
            fallback = {
                "score": 50,
                "confidence": 0.2,
                "verdict": "hold",
                "reasoning": f"API 调用失败: {error_msg[:100]}",
                "risk_flags": ["API_ERROR"],
                "suggestions": ["稍后重试"],
            }
            return json.dumps(fallback)

    def evaluate(self, proposal: TradeProposal, market_context: dict = None) -> Evaluation:
        prompt = self._build_user_prompt(proposal)
        raw = self._call_api(prompt)

        try:
            data = self._parse_response(raw)
        except (json.JSONDecodeError, KeyError) as e:
            data = {
                "score": 50,
                "confidence": 0.3,
                "verdict": "hold",
                "reasoning": f"解析失败: {e}",
                "risk_flags": ["PARSE_ERROR"],
                "suggestions": [],
            }

        return Evaluation(
            model_name=self._name,
            score=float(data.get("score", 50)),
            confidence=float(data.get("confidence", 0.5)),
            verdict=str(data.get("verdict", "hold")),
            reasoning=str(data.get("reasoning", "")),
            risk_flags=list(data.get("risk_flags", [])),
            suggestions=list(data.get("suggestions", [])),
        )


# ============================================================
# 工厂函数 — 创建标准评估委员会
# ============================================================

def create_standard_committee(api_key: str = None) -> list[ModelEvaluator]:
    """
    创建标准 4 模型评审委员会。

    api_key: DeepSeek API key。如果为 None，则尝试从环境变量读取。
    """
    if api_key is None:
        import os
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    if not api_key:
        # 没有 API key 时，创建模拟评估器
        return [create_mock_evaluator(name, sys_prompt)
                for name, sys_prompt in [
                    ("deepseek-fundamental", SYSTEM_FUNDAMENTAL),
                    ("deepseek-trader", SYSTEM_TRADER),
                    ("deepseek-risk", SYSTEM_RISK),
                    ("deepseek-local", SYSTEM_LOCAL),
                ]]

    return [
        DeepSeekEvaluator(
            name="deepseek-fundamental",
            system_prompt=SYSTEM_FUNDAMENTAL,
            api_key=api_key,
            model="deepseek-chat",
            temperature=0.3,
        ),
        DeepSeekEvaluator(
            name="deepseek-trader",
            system_prompt=SYSTEM_TRADER,
            api_key=api_key,
            model="deepseek-chat",
            temperature=0.5,
        ),
        DeepSeekEvaluator(
            name="deepseek-risk",
            system_prompt=SYSTEM_RISK,
            api_key=api_key,
            model="deepseek-chat",
            temperature=0.2,
        ),
        DeepSeekEvaluator(
            name="deepseek-local",
            system_prompt=SYSTEM_LOCAL,
            api_key=api_key,
            model="deepseek-chat",
            temperature=0.1,
        ),
    ]


def create_mock_evaluator(name: str, system_prompt: str) -> ModelEvaluator:
    """创建模拟评估器（用于开发/测试，不需要 API key）"""
    from .mock_evaluator import MockEvaluator
    return MockEvaluator(name=name, system_prompt=system_prompt)


# ============================================================
# Simple committee — 直接从提案到结果
# ============================================================

def run_committee(
    proposal: TradeProposal,
    api_key: str = None,
    evaluators: list[ModelEvaluator] = None,
) -> list[Evaluation]:
    """
    跑一次完整委员会评审。

    返回每个模型的评审结果列表。
    """
    if evaluators is None:
        evaluators = create_standard_committee(api_key)

    market_context = {
        "source_data": proposal.source_data or {},
        "timestamp": proposal.created_at,
    }

    results = []
    for ev in evaluators:
        result = ev.evaluate(proposal, market_context)
        results.append(result)

    return results
