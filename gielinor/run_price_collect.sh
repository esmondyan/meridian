#!/bin/bash
cd /home/hermes/projects/meridian/gielinor
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === OSRS Price Collect ===" >> ~/data/osrs/price_collect.log
python run.py 2>&1 | tail -3 >> ~/data/osrs/price_collect.log
echo "" >> ~/data/osrs/price_collect.log
echo "DONE"
