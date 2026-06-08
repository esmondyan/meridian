"""Stocksage Alpha daily incremental collection — cron wrapper."""
import os
import sys
from datetime import date

# Add project root to path
sys.path.insert(0, '/home/hermes/projects/stocksage/alpha')

from src.data.db import get_engine
from src.collector.collector import Collector
from sqlalchemy import text

def main():
    print(f"=== Stocksage Alpha 增量日线采集 ===")
    print(f"时间: {date.today()}")

    engine = get_engine()

    # Check trade calendar
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT is_open FROM trade_cal WHERE cal_date = :d"),
            {"d": date.today()}
        ).fetchone()

    if row is None:
        print(f"⚠️ 今日 {date.today()} 不在交易日历中，跳过采集。")
        return

    if row[0] == 0:
        print(f"🔒 今日 {date.today()} 非交易日，跳过采集。")
        return

    print(f"📈 今日 {date.today()} 是交易日，开始增量采集...")

    # Run incremental collection (last 10 days to catch weekends/holidays)
    c = Collector(source='sina')
    result = c.collect_incremental(days=10)

    # Print summary
    c.show_summary()

    # Extra detail: stock count in daily
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM daily")).scalar()
        stocks = conn.execute(
            text("SELECT COUNT(DISTINCT ts_code) FROM daily")
        ).scalar()
        latest = conn.execute(
            text("SELECT MAX(trade_date) FROM daily")
        ).scalar()

    print(f"\n📊 日线表详情:")
    print(f"  总记录数: {total:,}")
    print(f"  股票数:   {stocks}")
    print(f"  最新日期: {latest}")

    return result

if __name__ == '__main__':
    main()
