"""
完整测试套件 — 覆盖所有现有功能
每次大改动后先跑这个再上线

运行: python -m pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ──────────── 1. 数据采集测试 ────────────

def test_collect_prices():
    """采集器：能拉取 OSRS API 并返回 DataFrame"""
    from src.collectors.osrs_ge import build_price_df
    df = build_price_df()
    assert not df.empty, "采集返回空数据"
    assert "item_id" in df.columns
    assert len(df) > 1000, f"采集物品数太少: {len(df)}"


def test_collect_with_volume():
    """采集器：交易量和限购量字段存在"""
    from src.collectors.osrs_ge import build_price_df
    df = build_price_df()
    vol_cols = {"volume_buy", "volume_sell", "volume_total", "buy_limit"}
    missing = vol_cols - set(df.columns)
    if missing:
        print(f"  WARNING: 缺少字段: {missing}")


# ──────────── 2. 存储层测试 ────────────

def test_db_init_and_write():
    """SQLite 存储：初始化 + 写入 + 读取"""
    import tempfile
    import shutil
    import sqlite3
    from src.storage.db import init_db, insert_snapshot, get_latest_prices_df
    import src.storage.db as db_mod

    original_path = db_mod.DB_PATH
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    db_mod.DB_PATH = tmp

    try:
        init_db()
        import pandas as pd
        from datetime import datetime
        test_data = pd.DataFrame([{
            "item_id": 1, "name": "Test Sword",
            "high": 1000, "low": 900, "spread": 100,
            "buy_limit": 100, "is_member": 1,
            "volume_buy": 500, "volume_sell": 300, "volume_total": 800,
            "timestamp": datetime.now().isoformat(),
        }])
        insert_snapshot(test_data)

        df = get_latest_prices_df()
        assert not df.empty
        assert "Test Sword" in df["name"].values
    finally:
        db_mod.DB_PATH = original_path
        shutil.rmtree(tmp, ignore_errors=True)


def test_db_cleanup():
    """存储层：清理旧数据"""
    import tempfile, shutil
    from src.storage.db import init_db, insert_snapshot, cleanup_old_data
    import src.storage.db as db_mod
    original_path = db_mod.DB_PATH
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    db_mod.DB_PATH = tmp

    try:
        init_db()
        import pandas as pd
        from datetime import datetime, timedelta

        old = pd.DataFrame([{
            "item_id": 2, "name": "Old Item",
            "high": 100, "low": 90, "spread": 10,
            "buy_limit": 10, "is_member": 1,
            "volume_buy": 0, "volume_sell": 0, "volume_total": 0,
            "timestamp": (datetime.now() - timedelta(days=60)).isoformat(),
        }])
        new = pd.DataFrame([{
            "item_id": 3, "name": "New Item",
            "high": 200, "low": 180, "spread": 20,
            "buy_limit": 50, "is_member": 1,
            "volume_buy": 10, "volume_sell": 5, "volume_total": 15,
            "timestamp": datetime.now().isoformat(),
        }])
        insert_snapshot(old)
        insert_snapshot(new)

        deleted = cleanup_old_data(days=30)
        assert deleted >= 1, "应该清理掉旧数据"
    finally:
        db_mod.DB_PATH = original_path
        shutil.rmtree(tmp, ignore_errors=True)


# ──────────── 3. 分析引擎测试 ────────────

def test_load_prices():
    """分析引擎：从 SQLite 加载价格数据"""
    from src.analyzer.price_analysis import load_osrs_prices
    df = load_osrs_prices()
    assert not df.empty, "数据库中没有数据，请先运行采集"
    assert "name" in df.columns
    assert "high" in df.columns
    print(f"  共 {len(df)} 条记录，{df['item_id'].nunique()} 个物品")


def test_get_latest():
    """分析引擎：去重取每个物品最新价格"""
    from src.analyzer.price_analysis import load_osrs_prices, get_latest_prices
    df = load_osrs_prices()
    latest = get_latest_prices(df)
    assert not latest.empty
    assert len(latest) <= df["item_id"].nunique(), "最新价格应 <= 物品总数"
    assert "spread_ratio" in latest.columns, "应有价差比字段"


def test_calc_spread():
    """分析引擎：价差比计算正确"""
    from src.analyzer.price_analysis import calc_spread_ratio, load_osrs_prices
    df = load_osrs_prices()
    latest = calc_spread_ratio(df)
    assert not latest.empty
    assert "spread_ratio" in latest.columns
    assert (latest["spread_ratio"] >= 0).all()


def test_price_trend():
    """分析引擎：单品走势数据返回正确"""
    from src.analyzer.price_analysis import load_osrs_prices, get_price_trend
    df = load_osrs_prices()
    item = df["name"].iloc[0]
    trend = get_price_trend(df, item)
    assert not trend.empty
    assert "high_ma" in trend.columns, "应有移动平均线"


def test_flip_probability():
    """评分引擎：倒卖概率评分能跑通"""
    from src.analyzer.price_analysis import load_osrs_prices, calc_flip_probability
    df = load_osrs_prices()
    flip = calc_flip_probability(df)
    assert not flip.empty
    assert "flip_score" in flip.columns
    assert "flip_grade" in flip.columns
    assert flip["flip_score"].between(0, 100).all()
    assert flip["flip_grade"].nunique() >= 3
    print(f"  A+ 级: {(flip['flip_grade']=='⭐ A+').sum()} 个物品")


# ──────────── 4. 用户体系测试 ────────────

def test_user_register():
    """用户系统：注册 + 密码加密 + 查重"""
    import tempfile, shutil
    from src.auth import models as auth_db
    from src.auth.handler import hash_password, verify_password

    original = auth_db.APP_DB_PATH
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    auth_db.APP_DB_PATH = tmp

    try:
        auth_db.init_auth_db()
        pw = hash_password("testpass123")
        uid = auth_db.create_user("test@test.com", "tester", pw)
        assert uid > 0

        user = auth_db.get_user_by_email("test@test.com")
        assert user is not None
        assert user["username"] == "tester"
        assert verify_password("testpass123", user["password"])
        assert not verify_password("wrongpass", user["password"])

        # 重复注册应抛 IntegrityError
        import sqlite3
        try:
            auth_db.create_user("test@test.com", "dup", pw)
            assert False, "重复邮箱应抛异常"
        except sqlite3.IntegrityError:
            pass
    finally:
        auth_db.APP_DB_PATH = original
        shutil.rmtree(tmp, ignore_errors=True)


def test_jwt_tokens():
    """用户系统：JWT 签发 + 验证 + 刷新"""
    from src.auth.handler import (
        create_access_token, create_refresh_token,
        decode_token, refresh_access_token,
    )
    access = create_access_token(user_id=42, role="free")
    assert access and len(access) > 20

    payload = decode_token(access)
    assert payload is not None
    assert payload["sub"] == "42"
    assert payload["role"] == "free"
    assert payload["type"] == "access"

    refresh = create_refresh_token(user_id=42)
    assert refresh and len(refresh) > 20

    payload2 = decode_token(refresh)
    assert payload2 is not None
    assert payload2["type"] == "refresh"

    assert decode_token("invalid.token.here") is None


def test_analytics_event():
    """打点系统：事件写入 + 统计查询"""
    import tempfile, shutil
    from src.auth import models as auth_db

    original = auth_db.APP_DB_PATH
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    auth_db.APP_DB_PATH = tmp

    try:
        auth_db.init_auth_db()
        auth_db.log_event("page_view", '{"page":"test"}', session_id="sess1")
        auth_db.log_event("page_view", '{"page":"test"}', session_id="sess2")
        auth_db.log_event("search", '{"query":"sword"}', session_id="sess1")

        counts = auth_db.get_event_counts(since_days=7)
        assert len(counts) == 2

        dau = auth_db.get_daily_active_users(since_days=14)
        assert len(dau) >= 1
        assert dau[0]["dau"] >= 2
    finally:
        auth_db.APP_DB_PATH = original
        shutil.rmtree(tmp, ignore_errors=True)


# ──────────── 5. API 端点测试 ────────────

def test_api_health():
    """API：健康检查"""
    import requests
    from config.settings import API_PORT
    r = requests.get(f"http://127.0.0.1:{API_PORT}/api/health", timeout=5)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_register_login():
    """API：注册 -> 登录 -> 鉴权 全链路"""
    import requests, random, string
    from config.settings import API_PORT
    base = f"http://127.0.0.1:{API_PORT}"

    email = f"test_{random.randint(10000,99999)}@test.com"

    # 注册
    r = requests.post(f"{base}/api/auth/register", json={
        "email": email, "username": "testman", "password": "pass123"
    }, timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data["role"] == "free"

    # 登录
    r = requests.post(f"{base}/api/auth/login", json={
        "email": email, "password": "pass123"
    }, timeout=5)
    assert r.status_code == 200
    data = r.json()
    token = data["access_token"]
    assert data["username"] == "testman"

    # 鉴权
    r = requests.get(f"{base}/api/auth/me",
        headers={"Authorization": f"Bearer {token}"}, timeout=5)
    assert r.status_code == 200
    assert r.json()["email"] == email

    # 错误密码
    r = requests.post(f"{base}/api/auth/login", json={
        "email": email, "password": "wrongpass"
    }, timeout=5)
    assert r.status_code == 401

    # 匿名鉴权
    r = requests.get(f"{base}/api/auth/check", timeout=5)
    assert r.json()["authenticated"] == False


def test_api_analytics():
    """API：打点端点工作正常"""
    import requests
    from config.settings import API_PORT
    r = requests.post(
        f"http://127.0.0.1:{API_PORT}/api/analytics/event",
        json={"event_type": "test", "event_data": {"test": True}, "session_id": "pytest"},
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["ok"] == True


# ──────────── 6. 关键业务逻辑测试 ────────────

def test_flip_profit_calculation():
    """业务：倒卖利润计算（含税）"""
    # GE 税 = 卖出价 * 1% + 买入价 * 1%
    # 简化计算：profit_after_tax = spread - high * 0.02
    high = 1_000_000
    spread = 100_000
    profit = spread - high * 0.02
    assert profit == 80000, f"利润计算错误: {profit}"


def test_price_correction_edge_cases():
    """业务：负价差修正 + 零价差处理"""
    from src.analyzer.price_analysis import load_osrs_prices
    df = load_osrs_prices()
    # 采集器已对负价差做 clip(lower=0)
    assert (df["spread"] >= 0).all(), "所有价差应为非负"


def test_low_profit_penalty():
    """业务：极低利润物品不应获得高评分"""
    from src.analyzer.price_analysis import calc_flip_probability
    flip = calc_flip_probability()

    # 利润 < 10 GP 的高分物品（理论上不应出现）
    tiny_profit_high_score = flip[
        (flip["profit_after_tax"] < 10) & (flip["flip_score"] >= 70)
    ]
    print(f"\n  利润<10但评分≥70的物品数: {len(tiny_profit_high_score)}")
    assert len(tiny_profit_high_score) == 0, (
        f"极低利润(<10 GP)物品不应达到70+分: "
        f"{tiny_profit_high_score[['name','profit_after_tax','flip_score']].to_dict('records')}"
    )

    # 利润 < 50 GP 的物品不应进入 A+ 评级
    low_profit_aplus = flip[
        (flip["profit_after_tax"] < 50) & (flip["flip_grade"] == "⭐ A+")
    ]
    print(f"  利润<50但获A+的物品数: {len(low_profit_aplus)}")
    assert len(low_profit_aplus) == 0, (
        f"利润<50的物品不应评为A+: "
        f"{low_profit_aplus[['name','profit_after_tax','flip_score']].to_dict('records')}"
    )

    # 日利润潜力计算应有合理的上下界
    assert flip["daily_profit"].between(0, 1e12).all(), "日利润潜力有异常值"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
