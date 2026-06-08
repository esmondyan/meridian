#!/usr/bin/env python3
"""全量日线采集脚本 — 后台运行"""
import os, sys, time, json
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import akshare as ak
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import text
from tqdm import tqdm

from src.data.db import get_engine

engine = get_engine()

LOG = []

def log(msg):
    t = datetime.now().strftime('%H:%M:%S')
    print(f'[{t}] {msg}')
    LOG.append(f'[{t}] {msg}')

def get_codes():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT ts_code, symbol FROM stock_basic ORDER BY ts_code"
        )).fetchall()
    result = []
    for ts_code, symbol in rows:
        if ts_code.endswith('.SH'):
            result.append({'full': f'sh{symbol}', 'symbol': symbol})
        elif ts_code.endswith('.SZ'):
            result.append({'full': f'sz{symbol}', 'symbol': symbol})
        elif ts_code.endswith('.BJ'):
            result.append({'full': f'bj{symbol}', 'symbol': symbol})
    return result

def fetch_one(item):
    """采集单只股票"""
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_daily(
                symbol=item['full'],
                start_date='20200101',
                end_date='20260502',
                adjust='qfq'
            )
            if df is not None and not df.empty:
                std = pd.DataFrame({
                    'ts_code': item['symbol'],
                    'trade_date': pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d'),
                    'open': df['open'].values,
                    'close': df['close'].values,
                    'high': df['high'].values,
                    'low': df['low'].values,
                    'vol': df['volume'].values,
                    'amount': df['amount'].values,
                })
                with engine.begin() as conn:
                    std.to_sql('daily', conn, if_exists='append', index=False)
                return True
            return None
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                return str(e)[:80]
    return None

def main():
    log('🚀 全量日线采集启动')
    t_start = time.time()

    codes = get_codes()
    total = len(codes)
    log(f'共 {total} 只股票，开始采集...')

    success, skipped, failed = 0, 0, 0

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fetch_one, c): c['symbol'] for c in codes}
        with tqdm(total=total, desc='日线采集', unit='股') as pbar:
            for f in as_completed(futures):
                sym = futures[f]
                r = f.result()
                if r is True:
                    success += 1
                elif r is None:
                    skipped += 1
                else:
                    failed += 1
                    if failed <= 3:
                        log(f'  ❌ {sym}: {r}')
                pbar.update(1)

                # 每500只记录一次进度
                total_done = success + skipped + failed
                if total_done % 500 == 0:
                    elapsed = time.time() - t_start
                    rate = total_done / elapsed if elapsed > 0 else 0
                    eta = (total - total_done) / rate if rate > 0 else 0
                    log(f'  📊 {total_done}/{total} | 成功{success} 跳过{skipped} 失败{failed} | '
                        f'{rate:.1f}股/s | ETA {eta/60:.0f}分')

    elapsed = time.time() - t_start
    log(f'\n🏁 采集完成！')
    log(f'  成功: {success} | 跳过: {skipped} | 失败: {failed}')
    log(f'  耗时: {elapsed/60:.1f} 分钟')

    # 数据概览
    with engine.connect() as conn:
        total_rows = conn.execute(text('SELECT COUNT(*) FROM daily')).scalar()
        total_stocks = conn.execute(text('SELECT COUNT(DISTINCT ts_code) FROM daily')).scalar()
        min_d = conn.execute(text('SELECT MIN(trade_date) FROM daily')).scalar()
        max_d = conn.execute(text('SELECT MAX(trade_date) FROM daily')).scalar()
    log(f'\n📊 最终数据: {total_rows:,} 条 | {total_stocks} 只股票')
    log(f'  日期范围: {min_d} ~ {max_d}')

    # 写结果文件
    result = {
        'status': 'completed',
        'success': success,
        'skipped': skipped,
        'failed': failed,
        'total_rows': total_rows,
        'total_stocks': total_stocks,
        'elapsed_min': round(elapsed/60, 1),
        'log': LOG,
    }
    with open('/tmp/stocksage_alpha_full_pull.json', 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log('✅ 结果已保存到 /tmp/stocksage_alpha_full_pull.json')

if __name__ == '__main__':
    main()
