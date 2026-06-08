"""Wrapper to run run.py and log output to price_collect.log"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LOG_PATH = Path.home() / "data" / "osrs" / "price_collect.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

with open(LOG_PATH, "a") as log:
    log.write(f"[{timestamp}] === OSRS Price Collect ===\n")
    
    result = subprocess.run(
        [sys.executable, "run.py"],
        capture_output=True,
        text=True,
        timeout=600,
        cwd="/home/hermes/projects/meridian/gielinor"
    )
    
    # Write last 3 lines of combined stdout/stderr
    combined = (result.stdout + result.stderr).strip().split("\n")
    for line in combined[-3:]:
        log.write(line + "\n")
    
    log.write("\n")

print(f"Collection complete. Exit code: {result.returncode}")
print(f"Last 3 lines written to log.")
print(f"---")
for line in combined[-3:]:
    print(line)
