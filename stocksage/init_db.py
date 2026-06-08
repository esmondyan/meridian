#!/usr/bin/env python3
"""初始化数据库表"""
import os
import sys

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.data.db import verify_connection, get_engine
from src.data.models import Base


def main():
    print("🔧 初始化数据库表...")
    if not verify_connection():
        sys.exit(1)
    Base.metadata.create_all(get_engine())
    print("✅ 所有表已创建")
    from sqlalchemy import inspect
    tables = inspect(get_engine()).get_table_names()
    for t in tables:
        print(f"  📄 {t}")
    print(f"\n共 {len(tables)} 个表")


if __name__ == '__main__':
    main()
