"""
批量技术因子计算（向量化版本）
一次读取所有股票的近期日线数据，pandas groupby 并行计算
"""
import sys, os
sys.path.insert(0, '/home/hermes/projects/stocksage/alpha')
os.chdir('/home/hermes/projects/stocksage/alpha')
from dotenv import load_dotenv
load_dotenv()

import time
import pandas as pd
import numpy as np
from sqlalchemy import text
from src.data.db import get_engine
from src.factors.technical import (
    TechnicalFactorComputer, store_factor_wide,
    calc_ma, calc_rsi, calc_macd, calc_momentum, calc_volatility,
    calc_atr, calc_bollinger_width, calc_volume_ratio,
    calc_consecutive_down, calc_gap_down_pct, calc_lower_shadow_ratio,
    calc_price_position, calc_volume_spike, calc_stochastic,
    calc_williams_r, calc_turnover_proxy, calc_max_drawdown_20d,
    calc_daily_range, calc_ma_deviation,
)

def compute_all_factors_batch(engine, batch_size=200):
    """分批读取+计算，避免内存溢出"""
    # 先查最新已计算日期
    with engine.connect() as conn:
        latest = conn.execute(text(
            "SELECT MAX(trade_date) FROM factor_technical_wide"
        )).scalar()
    print(f"现有数据最新日期: {latest}")

    # 取所有有数据的股票
    with engine.connect() as conn:
        codes = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT ts_code FROM daily ORDER BY ts_code"
        )).fetchall()]
    total = len(codes)
    print(f"总股票数: {total}")

    computer = TechnicalFactorComputer()
    all_done = 0

    for start in range(0, total, batch_size):
        batch = codes[start:start+batch_size]
        t0 = time.time()
        df = computer.compute_batch(batch, limit_per_stock=500)
        elapsed = time.time() - t0

        if not df.empty:
            store_factor_wide(engine, df)
            all_done += len(batch)
            print(f"  [{start+batch_size}/{total}] {len(df)}行, {elapsed:.0f}s, "
                  f"预计剩余 {elapsed/batch_size*(total-start-batch_size)/60:.0f}min")

    print(f"\n✅ 全部完成: {all_done} 只股票")

if __name__ == '__main__':
    engine = get_engine()
    compute_all_factors_batch(engine, batch_size=100)
