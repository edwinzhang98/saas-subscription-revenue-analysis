"""Independent Python implementation agrees with SQL; the generator is deterministic."""
import hashlib
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def test_python_crosscheck_matches_sql(built):
    from saas_rev import crosscheck
    res = crosscheck.run()
    core = [c for c in res["comparisons"] if not c["informational"]]
    bad = [c["name"] for c in core if c["status"] != "match"]
    assert not bad, bad
    assert res["discrepancies"] == []


def _hash_tables(raw: Path) -> dict[str, str]:
    out = {}
    for p in sorted(raw.glob("*.parquet")):
        df = pd.read_parquet(p)
        out[p.name] = hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()
    return out


def test_generator_is_deterministic(built, tmp_path):
    """Same seed -> identical tables (a second, independent generation)."""
    import generate
    generate.generate(tmp_path / "raw2", built.seed, ROOT / "data_gen" / "params.yaml")
    assert _hash_tables(built.raw_dir) == _hash_tables(tmp_path / "raw2")


def test_generator_sanity_checks_pass(built):
    import sanity_checks
    res = sanity_checks.run(built.raw_dir)
    failed = [c["check"] for c in res["checks"] if not c["pass"]]
    assert not failed, failed


def test_warehouse_rebuild_is_deterministic(built):
    """Rebuilding the warehouse gives identical mart tables, row order and float bits included (single-threaded
    DuckDB + ORDER BY ALL on export). Downstream sampling (matched stayers) depends on this."""
    from saas_rev import marts, warehouse
    tables = ("fct_customer_mrr_monthly", "fct_mrr_movements", "fct_account_month_features", "rpt_nrr_grr_ttm",
              "rpt_mrr_waterfall_monthly", "dq_check_results")

    def snapshot() -> dict[str, str]:
        marts.export_warehouse()
        return {t: hashlib.sha256(pd.util.hash_pandas_object(pd.read_parquet(marts.path(t)), index=False).values.tobytes()).hexdigest()
                for t in tables}

    first = snapshot()
    warehouse.build(verbose=False)
    assert snapshot() == first
