# Meridian — 跨游戏自动化交易工厂

**子午线** 是一个 AI 驱动的游戏市场自动化交易平台。

## 子项目

| 项目 | 游戏 | 状态 | 核心能力 |
|------|------|:--:|----------|
| **Gielinor** | OSRS (Old School RuneScape) | 🟢 运行中 | 价格采集 · 倒卖评分引擎 · 竞品分析 |
| **New Eden** | EVE Online | 🟡 开发中 | 跨站套利 · AI 交易评审 · 自动下单(计划) |

## 架构

```
Meridian/
├── gielinor/          # OSRS 市场监控
│   ├── config/        # 评分权重 & 设置
│   ├── src/           # 采集器 · 分析器 · API · Dashboard
│   └── tests/         # pytest 测试
├── new-eden/          # EVE 自动交易
│   ├── auth/          # ESI SSO OAuth2
│   ├── trade_engine/  # 策略引擎 · 多模型评审 · 共识
│   └── data/          # SQLite 市场数据 (gitignored)
└── .github/           # CI/CD
```

## 快速开始

```bash
# OSRS
cd gielinor && pip install -r requirements.txt && streamlit run src/dashboard/app.py

# EVE
cd new-eden && pip install -r requirements.txt && streamlit run dashboard_eve.py --server.port 8900
```

## 环境变量

复制 `.env.example` → `.env`，填入 API keys。

```bash
cp .env.example .env
```
