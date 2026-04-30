#!/usr/bin/env python3
"""OSRS 价格采集器 — 供 crontab 调用"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.collectors.osrs_ge import collect_prices
from src.storage.db import insert_snapshot, init_db

init_db()
df = collect_prices()
insert_snapshot(df)
print(f"✅ 采集完成: {len(df)} 个物品")
