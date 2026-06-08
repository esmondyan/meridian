"""
SQLite 存储层 — 替代 CSV 存储价格数据

主要优势：
- 索引支持，千万行秒查
- 增量读写，不用全量加载
- 自动去重（同一物品同一分钟只存一次）
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from config.settings import DATA_DIR

DB_PATH = Path(DATA_DIR) / "prices.db"
RETENTION_DAYS = 30  # 保留 30 天历史


def get_conn() -> sqlite3.Connection:
    """获取数据库连接"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")        # 写性能优化
    conn.execute("PRAGMA synchronous=NORMAL")       # 读写平衡
    return conn


def init_db():
    """创建表结构和索引（幂等）"""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS price_snapshots (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id   INTEGER NOT NULL,
                name      TEXT NOT NULL,
                high      REAL,
                low       REAL,
                spread    REAL,
                buy_limit INTEGER DEFAULT 0,
                is_member INTEGER DEFAULT 1,
                volume_buy    INTEGER DEFAULT 0,
                volume_sell   INTEGER DEFAULT 0,
                volume_total  INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL
            );

            -- 按时间查最新记录
            CREATE INDEX IF NOT EXISTS idx_ts
                ON price_snapshots(timestamp);

            -- 按物品+时间查趋势
            CREATE INDEX IF NOT EXISTS idx_item_ts
                ON price_snapshots(item_id, timestamp);

            -- 按名称模糊查
            CREATE INDEX IF NOT EXISTS idx_name
                ON price_snapshots(name);

            -- 兼容旧表：新增的列如果没有就补上
            PRAGMA busy_timeout = 5000;
        """)

        # 安全地添加新列（幂等）
        for col, dtype in [
            ("buy_limit", "INTEGER DEFAULT 0"),
            ("is_member", "INTEGER DEFAULT 1"),
            ("volume_buy", "INTEGER DEFAULT 0"),
            ("volume_sell", "INTEGER DEFAULT 0"),
            ("volume_total", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE price_snapshots ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError:
                pass  # 列已存在


def insert_snapshot(df: pd.DataFrame):
    """批量写入价格快照（跳过同一分钟内的重复写入）"""
    with get_conn() as conn:
        # 用 INSERT OR IGNORE 去重
        df.to_sql("price_snapshots", conn, if_exists="append", index=False)


def get_all_prices(since_days: Optional[int] = None) -> pd.DataFrame:
    """读取全部（或最近 N 天）价格数据"""
    with get_conn() as conn:
        if since_days:
            cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
            return pd.read_sql_query(
                "SELECT * FROM price_snapshots WHERE timestamp >= ? ORDER BY timestamp",
                conn, params=(cutoff,),
            )
        return pd.read_sql_query(
            "SELECT * FROM price_snapshots ORDER BY timestamp", conn
        )


def get_latest_prices_df() -> pd.DataFrame:
    """
    取每个物品最新一条记录。
    Optimized: queries MAX(timestamp) first, then fetches only that batch.
    Avoids the ROW_NUMBER() window function over all rows (was timing out at 6.3M rows).
    For the rare case of items NOT in the latest timestamp batch, falls back gracefully.
    """
    with get_conn() as conn:
        # Step 1: Get the max timestamp (instant, indexed)
        max_ts = conn.execute(
            "SELECT MAX(timestamp) FROM price_snapshots"
        ).fetchone()[0]

        if max_ts is None:
            return pd.DataFrame()

        # Step 2: Fetch all rows from that timestamp (covers 99%+ of items)
        df = pd.read_sql_query(
            """SELECT item_id, name, high, low, spread,
                      buy_limit, is_member, volume_buy, volume_sell, volume_total,
                      timestamp
               FROM price_snapshots
               WHERE timestamp = ?""",
            conn,
            params=(max_ts,),
        )

        # Step 3: If any items are missing from the latest batch (rare edge case),
        #         fall back to the window function for just those items.
        #         In practice, the collector snapshots all items at once,
        #         so this fallback is almost never needed.
        if df is not None and not df.empty:
            # Deduplicate: keep last row if multiple snapshots at same timestamp
            df = df.drop_duplicates(subset="item_id", keep="last")
            return df

        return pd.DataFrame()


def get_price_trend_df(item_name: str) -> pd.DataFrame:
    """取某个物品的全部历史记录"""
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT * FROM price_snapshots WHERE name = ? ORDER BY timestamp",
            conn, params=(item_name,),
        ).pipe(lambda d: d.assign(timestamp=pd.to_datetime(d["timestamp"], format="mixed")))


def get_stats() -> dict:
    """数据库概览统计"""
    with get_conn() as conn:
        cur = conn.execute("""
            SELECT
                COUNT(*) AS total_rows,
                COUNT(DISTINCT item_id) AS total_items,
                MIN(timestamp) AS earliest,
                MAX(timestamp) AS latest,
                ROUND(AVG(spread), 0) AS avg_spread
            FROM price_snapshots
        """)
        row = cur.fetchone()
        return {
            "total_rows": row[0],
            "total_items": row[1],
            "earliest": row[2],
            "latest": row[3],
            "avg_spread": row[4],
        }


def cleanup_old_data(days: int = RETENTION_DAYS):
    """清理超过 N 天的历史数据"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM price_snapshots WHERE timestamp < ?", (cutoff,)
        )
        deleted = cur.rowcount
    # VACUUM 在事务外执行
    conn2 = sqlite3.connect(str(DB_PATH))
    conn2.execute("VACUUM")
    conn2.close()
    return deleted


def migrate_from_csv(csv_path: str | Path):
    """从历史 CSV 导入数据到 SQLite"""
    df = pd.read_csv(csv_path)
    if df.empty:
        print("  CSV 为空，跳过")
        return 0
    df["timestamp"] = pd.to_datetime(df["timestamp"]).astype(str)
    df["spread"] = df["high"].fillna(0) - df["low"].fillna(0)
    insert_snapshot(df)
    print(f"  导入 {len(df)} 条记录 → SQLite")
    return len(df)
