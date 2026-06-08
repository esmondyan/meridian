import sys
from pathlib import Path

sys.path.insert(0, "/home/hermes/projects/meridian/gielinor")
from src.analyzer.price_analysis import calc_flip_probability
from datetime import datetime

today = datetime.now().strftime("%Y-%m-%d")
result = calc_flip_probability()
result.to_csv(f"/home/hermes/projects/meridian/gielinor/data/scoring_{today}.csv", index=False)
print(f"评分完成: {len(result)} 个物品")
print(f"评分≥60: {(result['flip_score']>=60).sum()}")
