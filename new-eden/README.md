# Meridian: New Eden

**New Eden**（新伊甸）—— EVE Online 自动化交易引擎。

Meridian 子午线项目的一部分。详见 `../README.md`。

> New Eden 是 EVE Online 宇宙的星系团名，Jita 4-4 是全宇宙最繁忙的交易枢纽。

---

## 模块

```
new-eden/
├── trade_engine/          # 交易引擎核心
│   ├── proposal.py        # 交易提案数据结构
│   ├── strategy.py        # 策略引擎（价差/利润率/深度）
│   ├── evaluator.py       # 多模型评估委员会
│   ├── consensus.py       # 共识聚合
│   └── pipeline.py        # 流水线编排
├── backtest/              # 回测框架
│   └── backtest_engine.py
├── data/                  # 订单簿 + 纸面交易记录
├── collect_station.py     # Jita 站内挂单全量采集
├── verify_cron.py         # 纸面交易状态验证
├── dashboard_eve.py       # Streamlit 看板
└── trade_engine/
    └── config/
        ├── trading_config.json
        └── config_backtest.json
```

## 启动

```bash
source venv/bin/activate

# 采集当前 Jita 市场
python collect_station.py

# 启动交易看板
streamlit run dashboard_eve.py --server.port=8900
```
