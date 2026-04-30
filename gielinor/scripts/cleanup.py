#!/usr/bin/env python3
"""清理 30 天前的旧数据"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.storage.db import cleanup_old_data
deleted = cleanup_old_data(days=30)
print(f"✅ 清理完成: 删除 {deleted} 条过期数据")
