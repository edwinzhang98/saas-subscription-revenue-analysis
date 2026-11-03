"""Test fixtures: build the pipeline once into a temporary data directory, so tests never touch
the configured data directory (SAAS_DATA_DIR override)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "data_gen"))


def _settings_for(data_dir: Path):
    os.environ["SAAS_DATA_DIR"] = str(data_dir)
    from saas_rev import config
    config.get_settings.cache_clear()
    return config.get_settings()


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """Generate data and build the warehouse once in a temp directory."""
    import generate
    from saas_rev import warehouse
    data_dir = tmp_path_factory.mktemp("data")
    s = _settings_for(data_dir)
    generate.generate(s.raw_dir, s.seed, ROOT / "data_gen" / "params.yaml")
    warehouse.build(verbose=False)
    yield s
    os.environ.pop("SAAS_DATA_DIR", None)


@pytest.fixture(scope="session")
def con(built):
    import duckdb
    c = duckdb.connect(str(built.warehouse_path), read_only=True)
    yield c
    c.close()
