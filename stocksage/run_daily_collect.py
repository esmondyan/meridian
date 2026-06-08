#!/usr/bin/env python
"""Daily incremental data collection for Stocksage Alpha."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date, timedelta
from src.collector.collector import Collector

# Collect last 5 calendar days (covers 3 trading days: 05-15 Fri, 05-18 Mon, 05-19 Tue)
c = Collector(source='sina')
end = date.today()
start = end - timedelta(days=5)
print(f'采集范围: {start} ~ {end}')
result = c.collect_daily(start_date=start.strftime('%Y-%m-%d'), end_date=end.strftime('%Y-%m-%d'))
print(f'采集完成: success={result["success"]}, failed={len(result["failed"])}')

# Summary queries
from src.data.db import get_engine
from sqlalchemy import text

engine = get_engine()
with engine.connect() as conn:
    total = conn.execute(text('SELECT COUNT(*) FROM daily')).scalar()
    stocks = conn.execute(text('SELECT COUNT(DISTINCT ts_code) FROM daily')).scalar()
    latest = conn.execute(text('SELECT MAX(trade_date) FROM daily')).scalar()
    print(f'daily总条数={total}')
    print(f'daily股票数={stocks}')
    print(f'最新日期={latest}')

    sql = "SELECT trade_date, COUNT(*) as cnt FROM daily WHERE trade_date >= :sd GROUP BY trade_date ORDER BY trade_date DESC"
    rows = conn.execute(text(sql), {'sd': start}).fetchall()
    for r in rows:
        print(f'  {r[0]}: {r[1]}条')
