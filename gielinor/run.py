"""
定时采集入口

使用方式:
  python run.py              # 单次采集
  python run.py --watch     # 持续采集（按配置间隔）
"""

import sys
import time
import argparse
from datetime import datetime

from src.collectors.osrs_ge import run_once as collect_osrs
from config.settings import COLLECT_INTERVAL


def run_all():
    """跑所有采集器"""
    print(f"\n{'='*50}")
    print(f"  Game Price Monitor - {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"{'='*50}\n")
    collect_osrs()
    print("✅ 完成\n")


def watch():
    """持续采集模式"""
    print(f"🔄 持续采集，间隔 {COLLECT_INTERVAL} 秒")
    while True:
        run_all()
        print(f"⏳ 等待 {COLLECT_INTERVAL} 秒后下次采集...")
        time.sleep(COLLECT_INTERVAL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="持续采集")
    args = parser.parse_args()

    if args.watch:
        watch()
    else:
        run_all()
