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
