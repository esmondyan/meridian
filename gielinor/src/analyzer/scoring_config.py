"""评分配置加载器 — 读取 config/scoring_*.json

所有评分维度权重、分桶边界、评级阈值都从这里读取。
修改 JSON 后 → 重启 Streamlit 看板生效。
"""

import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
DEFAULT_CONFIG = "scoring_v35.json"


def load_scoring_config(name: str = DEFAULT_CONFIG) -> dict[str, Any]:
    """加载评分配置，失败时抛异常（宁可崩也别静默回退）"""
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"评分配置文件不存在: {path}")
    with open(path) as f:
        cfg = json.load(f)
    _validate(cfg)
    return cfg


def _validate(cfg: dict):
    """基础校验：关键字段必须存在"""
    assert "weights" in cfg, "配置缺少 weights"
    assert "bins" in cfg, "配置缺少 bins"
    assert "thresholds" in cfg, "配置缺少 thresholds"
    # 校验分桶标签数 = 桶数-1
    for key in ["vol", "limit", "tax", "roc"]:
        bins = cfg["bins"][key]
        labels = cfg["bins"][f"{key}_labels"]
        assert len(labels) == len(bins) - 1, \
            f"{key}: 标签数({len(labels)}) != 桶数-1({len(bins)-1})"


def replace_inf(seq: list) -> list:
    """把 JSON 里的 'inf' 字符串转成 float('inf')"""
    return [float("inf") if x == "inf" else x for x in seq]
