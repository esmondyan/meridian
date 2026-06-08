#!/usr/bin/env python3
"""
DB sync scripts — dispatched by Hermes, run on local worker.
Modes: init (full dump) | delta (incremental) | check (verify sync status)

Tunnel must be active: ssh -L 15432:127.0.0.1:5432 hermes@<cloud_ip>
"""
import os, sys, subprocess, json
from datetime import date, datetime

CLOUD_PG = {
    'host': '127.0.0.1',
    'port': 15432,
    'user': 'hermes',
    'password': os.environ.get('PGPASSWORD', 'hermes_quant_2026'),
    'dbname': 'stocksage_alpha',
}

LOCAL_PG = {
    'host': '127.0.0.1',
    'port': 5432,  # local docker PG
    'user': 'hermes',
    'password': 'hermes_quant_2026',
    'dbname': 'stocksage_alpha',
}

# Tables to sync (with their date column for deltas)
TABLES = {
    'daily': 'trade_date',
    'factor_technical_wide': 'trade_date',
    'fwd_returns': 'trade_date',
    'daily_basic': 'trade_date',
    'fina_indicator': 'end_date',
    'stock_basic': None,  # full sync always
}

def pg_connect(conf):
    import psycopg2
    return psycopg2.connect(**conf)

def check_tunnel():
    """Verify SSH tunnel is working"""
    try:
        conn = pg_connect(CLOUD_PG)
        conn.close()
        return True
    except:
        print("❌ Cannot connect to cloud PG via tunnel. Is SSH tunnel active?")
        print("   Run: ssh -N -L 15432:127.0.0.1:5432 hermes@<cloud_ip>")
        return False

def init_sync():
    """One-time full sync: dump cloud → restore to local"""
    if not check_tunnel():
        return -1
    
    print("[INIT] Full database sync (one-time, ~30 min on 3Mbps)...")
    print("  This will recreate local tables with cloud data.")
    
    try:
        conn = pg_connect(LOCAL_PG)
        cur = conn.cursor()
        
        # Drop and recreate each table
        for table in TABLES:
            print(f"  [{table}] dumping...")
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            conn.commit()
        
        conn.close()
    except Exception as e:
        print(f"  ⚠️ Local PG not ready: {e}")
        print("  Make sure local Docker PG is running on port 5432")
        return -1
    
    # Use pg_dump + pg_restore through tunnel
    table_list = ' -t '.join(TABLES.keys())
    cmd = (
        f'pg_dump -h {CLOUD_PG["host"]} -p {CLOUD_PG["port"]} '
        f'-U {CLOUD_PG["user"]} -d {CLOUD_PG["dbname"]} '
        f'-t {table_list} '
        f'-Fc -Z9 --no-owner --no-privileges '
        f'| pg_restore -h {LOCAL_PG["host"]} -p {LOCAL_PG["port"]} '
        f'-U {LOCAL_PG["user"]} -d {LOCAL_PG["dbname"]} '
        f'-j 4 --no-owner --no-privileges'
    )
    
    env = os.environ.copy()
    env['PGPASSWORD'] = CLOUD_PG['password']
    
    print(f"  Running pg_dump → pg_restore (may take 15-30 min)...")
    start = datetime.now()
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env, timeout=3600)
    
    elapsed = (datetime.now() - start).total_seconds()
    
    if result.returncode == 0:
        print(f"  ✅ Init sync complete ({elapsed:.0f}s)")
        return 0
    else:
        print(f"  ❌ Init sync failed (exit {result.returncode})")
        print(result.stderr[-2000:])
        return -1

def delta_sync():
    """Incremental sync: pull only new rows since local max date"""
    if not check_tunnel():
        return -1
    
    try:
        cloud = pg_connect(CLOUD_PG)
        local = pg_connect(LOCAL_PG)
    except Exception as e:
        print(f"❌ Cannot connect: {e}")
        return -1
    
    cloud_cur = cloud.cursor()
    local_cur = local.cursor()
    
    total_rows = 0
    
    for table, date_col in TABLES.items():
        if date_col is None:
            # Full sync for static tables
            cloud_cur.execute(f"SELECT COUNT(*) FROM {table}")
            cloud_count = cloud_cur.fetchone()[0]
            local_cur.execute(f"SELECT COUNT(*) FROM {table}")
            local_count = local_cur.fetchone()[0]
            if cloud_count != local_count:
                print(f"  [{table}] {local_count}→{cloud_count} (full refresh)")
                # Full refresh
                local_cur.execute(f"DELETE FROM {table}")
                cloud_cur.execute(f"SELECT * FROM {table}")
                cols = [d[0] for d in cloud_cur.description]
                for row in cloud_cur:
                    placeholders = ', '.join(['%s'] * len(row))
                    local_cur.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", row)
                local.commit()
                total_rows += cloud_count
            else:
                print(f"  [{table}] up to date ({cloud_count} rows)")
            continue
        
        # Get local max date
        local_cur.execute(f"SELECT MAX({date_col}) FROM {table}")
        local_max = local_cur.fetchone()[0]
        
        if local_max is None:
            # Local table empty, pull all
            print(f"  [{table}] empty locally, pulling all...")
            cloud_cur.execute(f"SELECT COUNT(*) FROM {table}")
            n = cloud_cur.fetchone()[0]
            
            # Batch pull
            offset = 0
            batch_size = 50000
            while offset < n:
                cloud_cur.execute(f"SELECT * FROM {table} ORDER BY {date_col} LIMIT {batch_size} OFFSET {offset}")
                cols = [d[0] for d in cloud_cur.description]
                rows = cloud_cur.fetchall()
                if not rows:
                    break
                for row in rows:
                    placeholders = ', '.join(['%s'] * len(row))
                    local_cur.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", row)
                local.commit()
                offset += len(rows)
                print(f"    {min(offset, n)}/{n}")
            total_rows += n
        
        else:
            # Pull rows after local_max
            cloud_cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {date_col} > %s", (local_max,))
            n = cloud_cur.fetchone()[0]
            
            if n == 0:
                print(f"  [{table}] up to date (max {date_col}={local_max})")
                continue
            
            print(f"  [{table}] {n} new rows since {local_max}")
            
            cloud_cur.execute(f"SELECT * FROM {table} WHERE {date_col} > %s ORDER BY {date_col}", (local_max,))
            cols = [d[0] for d in cloud_cur.description]
            for row in cloud_cur:
                placeholders = ', '.join(['%s'] * len(row))
                local_cur.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING", row)
            local.commit()
            total_rows += n
    
    cloud.close()
    local.close()
    
    print(f"✅ Delta sync complete: {total_rows} rows synced")
    return 0

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'delta'
    
    print(f"=== Hermes DB Sync — {mode} ===")
    print(f"  Cloud: {CLOUD_PG['host']}:{CLOUD_PG['port']}")
    print(f"  Local: {LOCAL_PG['host']}:{LOCAL_PG['port']}")
    
    if mode == 'init':
        sys.exit(init_sync())
    elif mode == 'delta':
        sys.exit(delta_sync())
    elif mode == 'check':
        print("0" if check_tunnel() else "1")
        sys.exit(0 if check_tunnel() else 1)
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
