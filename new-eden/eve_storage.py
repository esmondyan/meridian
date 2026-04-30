"""
EVE 多站市场数据存储层

替代每个站一个 CSV 的散养模式，统一用 SQLite 管理。
"""
from pathlib import Path
from datetime import datetime
import sqlite3

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "eve_markets.db"


def get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """创建统一的市场数据表（幂等）"""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS station_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                region      TEXT NOT NULL,       -- jita, amarr, dodixie, rens, hek
                type_id     INTEGER NOT NULL,
                name        TEXT NOT NULL,
                best_buy    REAL,
                best_sell   REAL,
                bid_ask_spread    REAL,
                spread_ratio      REAL,
                gross_margin      REAL,
                roc               REAL,
                best_buy_vol      INTEGER,
                best_sell_vol     INTEGER,
                total_buy_vol     INTEGER,
                total_sell_vol    INTEGER,
                buy_order_count   INTEGER,
                sell_order_count  INTEGER,
                station_score     REAL,
                collected_at TEXT NOT NULL
            );

            -- 按区域查
            CREATE INDEX IF NOT EXISTS idx_region
                ON station_snapshots(region, collected_at);

            -- 按物品查（跨区比价用）
            CREATE INDEX IF NOT EXISTS idx_type_id
                ON station_snapshots(type_id, region);

            -- 按时间范围查
            CREATE INDEX IF NOT EXISTS idx_collected
                ON station_snapshots(collected_at);

            PRAGMA busy_timeout = 5000;
        """)


def import_csv(region: str, csv_path: str | Path) -> int:
    """将单个 CSV 导入 SQLite，返回导入行数"""
    df = pd.read_csv(csv_path)
    now = datetime.now().isoformat(timespec="seconds")

    df["region"] = region
    df["collected_at"] = now

    # 取这个区域的最新数据（先删旧的全量再写新的）
    with get_conn() as conn:
        conn.execute("DELETE FROM station_snapshots WHERE region = ?", (region,))
        df.to_sql("station_snapshots", conn, if_exists="append", index=False)

    return len(df)


def get_latest(region: str = None, min_score: int = 0, limit: int = 50) -> pd.DataFrame:
    """查最新数据，支持按区域和评分过滤"""
    with get_conn() as conn:
        if region:
            sql = """
                SELECT * FROM station_snapshots
                WHERE region = ? AND station_score >= ?
                ORDER BY station_score DESC LIMIT ?
            """
            return pd.read_sql_query(sql, conn, params=(region, min_score, limit))
        else:
            # 所有区域的最新数据（每个区域取最新的 collected_at）
            sql = """
                SELECT s.* FROM station_snapshots s
                INNER JOIN (
                    SELECT region, MAX(collected_at) AS latest
                    FROM station_snapshots GROUP BY region
                ) t ON s.region = t.region AND s.collected_at = t.latest
                WHERE s.station_score >= ?
                ORDER BY s.station_score DESC LIMIT ?
            """
            return pd.read_sql_query(sql, conn, params=(min_score, limit))


def get_cross_region_arb(min_profit: float = 0, limit: int = 20) -> pd.DataFrame:
    """跨区套利：两种策略

    策略一 即时成交（推荐运输）：
        A区按卖价直接买 → 运到B区按买价直接卖
        利：立等成交，不用等  弊：要承担运费+运输风险

    策略二 挂单套利：
        A区挂买单等 → B区挂卖单等
        利：运费低(不用真运)  弊：可能要等很久才成交
    """
    with get_conn() as conn:
        sql = """
            SELECT
                a.name,
                a.type_id,
                a.region AS buy_region,
                a.best_buy  AS buy_bid,           -- A区最高买价（挂单等卖）
                a.best_buy_vol  AS buy_bid_depth,
                a.best_sell AS buy_ask,            -- A区最低卖价（直接吃单买）
                a.best_sell_vol AS buy_ask_depth,
                b.region AS sell_region,
                b.best_buy  AS sell_bid,           -- B区最高买价（直接吃单卖）
                b.best_buy_vol  AS sell_bid_depth,
                b.best_sell AS sell_ask,           -- B区最低卖价（挂单等买）
                b.best_sell_vol AS sell_ask_depth,

                -- 挂单策略：在A区挂单等买→B区挂单等卖
                (b.best_sell - a.best_buy) AS spread_orderbook,
                CASE WHEN a.best_buy > 0
                     THEN (b.best_sell - a.best_buy) / a.best_buy * 100
                     ELSE 0 END AS margin_orderbook_pct,
                (b.best_sell - a.best_buy - b.best_sell * 0.033) AS profit_orderbook,

                -- 即时策略：A区直接买→运到B区直接卖（运输套利用）
                (b.best_buy - a.best_sell) AS spread_instant,
                CASE WHEN a.best_sell > 0
                     THEN (b.best_buy - a.best_sell) / a.best_sell * 100
                     ELSE 0 END AS margin_instant_pct,
                (b.best_buy - a.best_sell - b.best_buy * 0.033) AS profit_instant,

                MIN(a.best_sell_vol, b.best_buy_vol) AS max_tradeable
            FROM station_snapshots a
            JOIN station_snapshots b ON a.type_id = b.type_id AND a.region != b.region
            WHERE a.collected_at = (SELECT MAX(collected_at) FROM station_snapshots WHERE region = a.region)
              AND b.collected_at = (SELECT MAX(collected_at) FROM station_snapshots WHERE region = b.region)
              AND b.best_buy > a.best_sell
              AND a.best_sell > 0 AND b.best_buy > 0
              AND (b.best_buy - a.best_sell - b.best_buy * 0.033) >= ?
            ORDER BY profit_instant DESC
            LIMIT ?
        """
        return pd.read_sql_query(sql, conn, params=(min_profit, limit))


def get_stats() -> dict:
    """总览统计"""
    with get_conn() as conn:
        cur = conn.execute("""
            SELECT
                COUNT(DISTINCT region) AS total_regions,
                COUNT(*) AS total_rows,
                COUNT(DISTINCT type_id) AS total_items,
                MIN(collected_at) AS earliest,
                MAX(collected_at) AS latest
            FROM station_snapshots
        """)
        row = cur.fetchone()
        cur2 = conn.execute("""
            SELECT region, COUNT(*) AS cnt, MAX(collected_at) AS last_update
            FROM station_snapshots GROUP BY region ORDER BY region
        """)
        regions = [dict(r) for r in cur2.fetchall()]
        return {**dict(row), "regions": regions}


def get_item_across_regions(type_id: int = None, name_query: str = None) -> pd.DataFrame:
    """获取物品在所有区域的最新价格（跨站对比用）"""
    with get_conn() as conn:
        if type_id is not None:
            sql = """
                SELECT s.* FROM station_snapshots s
                INNER JOIN (
                    SELECT region, MAX(collected_at) AS latest
                    FROM station_snapshots GROUP BY region
                ) t ON s.region = t.region AND s.collected_at = t.latest
                WHERE s.type_id = ?
                ORDER BY s.region
            """
            return pd.read_sql_query(sql, conn, params=(type_id,))
        elif name_query:
            # 先找到匹配的物品 type_id（去重），再查全量
            lookups = pd.read_sql_query("""
                SELECT DISTINCT type_id, name FROM station_snapshots
                WHERE name LIKE ? LIMIT 20
            """, conn, params=(f"%{name_query}%",))
            if len(lookups) == 0:
                return pd.DataFrame()
            # 查第一个匹配物品的跨站数据
            tid = lookups.iloc[0]["type_id"]
            df = pd.read_sql_query("""
                SELECT s.* FROM station_snapshots s
                INNER JOIN (
                    SELECT region, MAX(collected_at) AS latest
                    FROM station_snapshots GROUP BY region
                ) t ON s.region = t.region AND s.collected_at = t.latest
                WHERE s.type_id = ?
                ORDER BY s.region
            """, conn, params=(tid,))
            df.attrs["lookups"] = lookups  # 存搜索结果供 UI 选择
            return df
    return pd.DataFrame()


def merge_all_csvs():
    """一键导入所有 CSV 到 SQLite"""
    init_db()
    total = 0
    for csv_path in sorted(DATA_DIR.glob("station_*_full.csv")):
        # 从文件名提取区域名: station_amarr_full.csv → amarr
        region = csv_path.stem.replace("station_", "").replace("_full", "")
        n = import_csv(region, csv_path)
        print(f"  {region:8s}: {n:>6,} 行")
        total += n
    print(f"  {'总计':8s}: {total:>6,} 行 → {DB_PATH}")
    return total


if __name__ == "__main__":
    merge_all_csvs()
    print(f"\n📊 SQLite 统计:")
    stats = get_stats()
    print(f"  区域: {stats['total_regions']} 个")
    print(f"  物品: {stats['total_items']:,} 种")
    print(f"  总行: {stats['total_rows']:,}")
    print(f"  最新: {stats['latest']}")
    for r in stats['regions']:
        print(f"    {r['region']:8s}: {r['cnt']:>7,} 行  更新: {r['last_update']}")
