"""Check trade calendar dates near today and verify if today is a trading day."""
import os, sys
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from src.data.db import get_engine
from sqlalchemy import text

engine = get_engine()
with engine.connect() as conn:
    # Most recent trading days
    latest = conn.execute(text(
        "SELECT cal_date, is_open FROM trade_cal "
        "WHERE cal_date >= CURRENT_DATE - INTERVAL '10 days' "
        "ORDER BY cal_date DESC"
    )).fetchall()
    print("=== Trade Cal (past 10 days) ===")
    for r in latest:
        marker = " <<< TODAY" if str(r[0]) == str(conn.execute(text("SELECT CURRENT_DATE")).scalar()) else ""
        print(f"  {r[0]}  is_open={r[1]}{marker}")
    
    today = conn.execute(text("SELECT CURRENT_DATE")).scalar()
    print(f"\nToday: {today}")
    
    # Check if today is in trade_cal
    row = conn.execute(
        text("SELECT is_open FROM trade_cal WHERE cal_date = CURRENT_DATE")
    ).fetchone()
    if row:
        print(f"Today is_open={row[0]}")
    else:
        print("Today NOT found in trade_cal — likely holiday/weekend")
        
        # Find most recent trading day
        last_trade = conn.execute(text(
            "SELECT cal_date FROM trade_cal "
            "WHERE cal_date < CURRENT_DATE AND is_open = 1 "
            "ORDER BY cal_date DESC LIMIT 1"
        )).fetchone()
        if last_trade:
            print(f"Most recent trading day: {last_trade[0]}")
