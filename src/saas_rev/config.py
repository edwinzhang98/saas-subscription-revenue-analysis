"""Project paths and settings.

All paths come from ``config.yaml`` at the repo root. The data directory can be
overridden with the ``SAAS_DATA_DIR`` environment variable (or a ``.env`` file),
so nothing in the code depends on a personal absolute path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    raw_dir: Path
    warehouse_path: Path
    marts_dir: Path
    as_of_date: date
    window_start: date
    seed: int

    @property
    def sql_dir(self) -> Path:
        return REPO_ROOT / "sql"

    @property
    def reports_dir(self) -> Path:
        return REPO_ROOT / "reports"

    @property
    def docs_dir(self) -> Path:
        return REPO_ROOT / "docs"

    @property
    def figures_dir(self) -> Path:
        return REPO_ROOT / "reports" / "figures"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv()
    cfg = yaml.safe_load((REPO_ROOT / "config.yaml").read_text())
    data_dir = Path(os.path.expanduser(os.environ.get("SAAS_DATA_DIR", cfg["data_dir"]))).resolve()
    return Settings(
        data_dir=data_dir,
        raw_dir=data_dir / cfg.get("raw_subdir", "raw"),
        warehouse_path=data_dir / cfg.get("warehouse_file", "warehouse.duckdb"),
        marts_dir=data_dir / cfg.get("marts_subdir", "marts"),
        as_of_date=date.fromisoformat(str(cfg["as_of_date"])),
        window_start=date.fromisoformat(str(cfg["window_start"])),
        seed=int(cfg["seed"]),
    )
