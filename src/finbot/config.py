"""Configuration loading.

Resolves a layered config: defaults <- config/config.yaml <- config/config.local.yaml.
Missing files are tolerated so the skeleton runs out of the box from the example.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a core dep but stay defensive
    yaml = None  # type: ignore

# Repo root = two levels up from this file (src/finbot/config.py -> repo).
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

_DEFAULTS: Dict[str, Any] = {
    "market": "A_SHARE",
    "data": {
        "source": "akshare",
        "fallback_to_mock": True,
        "cache_dir": "data/cache",
        "raw_dir": "data/raw",
        "processed_dir": "data/processed",
        "universe": {
            "exclude_st": True,
            "exclude_new_ipo_days": 60,
            "min_amount_yi": 0.5,
        },
        "news": {"sources": ["eastmoney", "sina", "cls"], "lookback_hours": 24},
    },
    "features": {
        "technical": True,
        "capital_flow": True,
        "sentiment": True,
        "board_linkage": True,
        "limitup_genes": True,
    },
    "model": {
        "type": "lightgbm",
        "task": "rank_limitup_probability",
        "store_dir": "models_store",
        "top_n": 20,
        "min_score": 0.15,
    },
    "strategy": {
        "portfolio_file": "config/portfolio.json",
        "risk": {
            "max_position_pct": 0.15,
            "max_new_positions": 3,
            "stop_loss_pct": -0.07,
            "take_profit_pct": 0.15,
            "max_total_exposure": 0.90,
        },
        "style": "balanced",
    },
    "report": {"output_dir": "reports", "format": "markdown"},
    "llm": {"model": "claude-opus-4-8"},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class Config:
    """Dict-backed config with dotted-path access and resolved paths."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, dotted: str, default: str = "") -> Path:
        """Resolve a config value that names a path, relative to the repo root."""
        raw = self.get(dotted, default)
        p = Path(raw)
        return p if p.is_absolute() else (REPO_ROOT / p)

    @property
    def raw(self) -> Dict[str, Any]:
        return self._data


def load_config(explicit_path: str | None = None) -> Config:
    """Load layered configuration.

    Order (later wins): built-in defaults, config/config.yaml (or example),
    config/config.local.yaml, and an explicitly provided path.
    """
    merged = dict(_DEFAULTS)

    primary = CONFIG_DIR / "config.yaml"
    if not primary.exists():
        primary = CONFIG_DIR / "config.example.yaml"
    merged = _deep_merge(merged, _load_yaml(primary))
    merged = _deep_merge(merged, _load_yaml(CONFIG_DIR / "config.local.yaml"))

    if explicit_path:
        merged = _deep_merge(merged, _load_yaml(Path(explicit_path)))

    # Environment override for the data source is handy in CI / web sessions.
    if os.environ.get("FINBOT_DATA_SOURCE"):
        merged.setdefault("data", {})["source"] = os.environ["FINBOT_DATA_SOURCE"]

    return Config(merged)
