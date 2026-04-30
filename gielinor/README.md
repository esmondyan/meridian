# Meridian: Gielinor

**Gielinor**（吉莱诺）—— Old School RuneScape 市场价格监控与评分系统。

Meridian 子午线项目的一部分。详见 `../README.md`。

> Gielinor 是 OSRS 的世界名称，支撑着 Grand Exchange 的经济命脉。

---

## 项目结构

```
gielinor/
├── src/
│   ├── collectors/
│   │   ├── osrs_ge.py         # OSRS 官方 API 采集
│   │   └── g2g_scraper.py     # G2G 平台价格抓取
│   ├── analyzer/
│   │   └── price_analysis.py  # 价格分析逻辑
│   └── dashboard/
│       └── app.py             # Streamlit 看板
├── data/                      # 数据存放
├── config/
│   └── settings.py            # 配置
├── scoring_v35.py             # 评分引擎
├── dashboard.py               # 主看板
└── requirements.txt
```

## 启动

```bash
source venv/bin/activate

# 采集 OSRS 物品价格
python run.py

# 运行评分
python scoring_v35.py

# 启动看板
streamlit run src/dashboard/app.py --server.port=8899
```
