#!/usr/bin/env python3
"""
Local worker — run-once mode for non-24/7 machines.

Usage (Windows PowerShell, run when you start work):
  $env:HERMES_CLOUD = "云IP"
  python local_worker.py

What it does:
  1. Pull all pending tasks from cloud via scp
  2. Run delta DB sync first
  3. Process all queued tasks FIFO
  4. Push results back to cloud
  5. Exit when queue is empty

SSH tunnel must be active: ssh -N -L 15432:127.0.0.1:5432 hermes@云IP
"""
import os, sys, json, time, subprocess
from pathlib import Path
from datetime import datetime

CLOUD_HOST = os.environ.get('HERMES_CLOUD')
CLOUD_USER = os.environ.get('HERMES_USER', 'hermes')

if not CLOUD_HOST:
    print("Set HERMES_CLOUD environment variable first.")
    print("  $env:HERMES_CLOUD = 'your-cloud-ip'")
    sys.exit(1)

TASK_DIR = Path.home() / 'tasks'
for d in ['pending', 'running', 'done', 'failed']:
    (TASK_DIR / d).mkdir(parents=True, exist_ok=True)

def scp_pull(remote_path, local_path):
    """Pull files from cloud"""
    cmd = f'scp -r -q {CLOUD_USER}@{CLOUD_HOST}:{remote_path} {local_path}'
    return subprocess.run(cmd, shell=True, capture_output=True, timeout=30)

def scp_push(local_file, remote_path):
    """Push file to cloud"""
    cmd = f'scp -q {local_file} {CLOUD_USER}@{CLOUD_HOST}:{remote_path}'
    return subprocess.run(cmd, shell=True, capture_output=True, timeout=30)

def run_script(script_path, args, workdir, timeout_s=3600):
    """Execute a Python script with args"""
    env = os.environ.copy()
    env['PGHOST'] = '127.0.0.1'
    env['PGPORT'] = '15432' if '--local' not in args else '5432'
    
    cmd = [sys.executable, script_path] + args
    print(f"  → {' '.join(cmd)}")
    start = time.time()
    
    result = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=workdir, env=env, timeout=timeout_s)
    
    elapsed = time.time() - start
    return {
        'exit_code': result.returncode,
        'stdout': result.stdout[-50000:],
        'stderr': result.stderr[-10000:],
        'elapsed_s': elapsed,
    }

print("═" * 50)
print(f"Hermes Local Worker — {datetime.now():%Y-%m-%d %H:%M}")
print(f"Cloud: {CLOUD_USER}@{CLOUD_HOST}")
print("═" * 50)

# ═══════════════════════════════════════
# Step 1: Pull pending tasks
# ═══════════════════════════════════════
print("\n[1/3] Pulling tasks...")
scp_pull(f'tasks/pending/*.json', f'{TASK_DIR}/pending/')

pending = sorted((TASK_DIR / 'pending').glob('*.json'))
if not pending:
    print("  No pending tasks. Already synced?")
    
    # Still run delta sync
    print("\n[2/3] Running delta sync anyway...")
    outcome = run_script(
        str(Path.home() / 'db_sync.py'), ['delta'],
        str(Path.home()), 300
    )
    if outcome['exit_code'] == 0:
        print("  ✅ DB up to date")
    else:
        print(f"  ⚠️ Sync issue: {outcome['stderr'][:200]}")
    
    print("\n[3/3] Queue empty — done.")
    sys.exit(0)

print(f"  Found {len(pending)} task(s):")
for p in pending:
    with open(p) as f:
        task = json.load(f)
    print(f"    • {task['name']}")

# ═══════════════════════════════════════
# Step 2: Delta sync first
# ═══════════════════════════════════════
print(f"\n[2/3] Delta DB sync...")
outcome = run_script(
    str(Path.home() / 'db_sync.py'), ['delta'],
    str(Path.home()), 300
)
if outcome['exit_code'] == 0:
    print("  ✅ DB up to date")
else:
    print(f"  ⚠️ Sync issue, continuing anyway...")

# ═══════════════════════════════════════
# Step 3: Process tasks
# ═══════════════════════════════════════
print(f"\n[3/3] Processing {len(pending)} task(s)...")

for task_file in pending:
    with open(task_file) as f:
        task = json.load(f)
    
    name = task['name']
    script = task['script']
    args = task.get('args', [])
    workdir = task.get('workdir', str(Path.home()))
    
    print(f"\n  ── {name} ──")
    outcome = run_script(script, args, workdir, 
                        task.get('requirements', {}).get('timeout_s', 3600))
    
    # Write result
    status = 'done' if outcome['exit_code'] == 0 else 'failed'
    result = {**task, **outcome, 'completed_at': time.time()}
    
    result_file = TASK_DIR / status / f'{name}.json'
    with open(result_file, 'w') as f:
        json.dump(result, f, indent=2)
    
    # Push to cloud
    scp_push(result_file, f'tasks/{status}/')
    
    # Clean up pending
    task_file.unlink()
    
    emoji = '✅' if outcome['exit_code'] == 0 else '❌'
    print(f"  {emoji} {name} ({outcome['elapsed_s']:.0f}s)")

print(f"\n{'═' * 50}")
print("All done. Worker exiting.")
print(f"{'═' * 50}")
