"""Build the DuckDB warehouse: load raw parquet and run every sql/NN_*.sql file in order.

Usage:
    python -m saas_rev.warehouse                      # full build
    python -m saas_rev.warehouse --file sql/25_rpt_nrr_grr_ttm.sql   # run one file against the warehouse
    python -m saas_rev.warehouse --check-standalone   # run each file on its own (fresh connection) after a build
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from .config import get_settings


def sql_files() -> list[Path]:
    s = get_settings()
    return sorted(p for p in s.sql_dir.glob("[0-9][0-9]_*.sql"))


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(s.warehouse_path), read_only=read_only)
    return con


def _set_variables(con: duckdb.DuckDBPyConnection) -> None:
    s = get_settings()
    con.execute(f"SET VARIABLE raw_dir = '{s.raw_dir.as_posix()}'")
    con.execute(f"SET VARIABLE as_of_date = DATE '{s.as_of_date.isoformat()}'")


def run_file(con: duckdb.DuckDBPyConnection, path: Path) -> float:
    t0 = time.time()
    con.execute(path.read_text())
    return time.time() - t0


def build(verbose: bool = True) -> dict[str, float]:
    s = get_settings()
    if s.warehouse_path.exists():
        s.warehouse_path.unlink()
    con = connect()
    _set_variables(con)
    timings = {}
    for f in sql_files():
        timings[f.name] = run_file(con, f)
        if verbose:
            print(f"  {f.name:<45} {timings[f.name]:6.2f}s")
    con.close()
    return timings


def check_standalone() -> list[tuple[str, bool, str]]:
    """Each SQL file must run on its own against the built warehouse (fresh connection, no session state
    except the raw_dir variable that 00_load_raw.sql documents as required)."""
    results = []
    for f in sql_files():
        con = connect()
        try:
            if f.name.startswith("00_"):
                _set_variables(con)
            run_file(con, f)
            results.append((f.name, True, ""))
        except Exception as e:  # noqa: BLE001
            results.append((f.name, False, str(e).splitlines()[0]))
        finally:
            con.close()
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=None)
    ap.add_argument("--check-standalone", action="store_true")
    args = ap.parse_args()
    if args.file:
        con = connect()
        _set_variables(con)
        print(f"{args.file}: {run_file(con, args.file):.2f}s")
        con.close()
    elif args.check_standalone:
        for name, ok, err in check_standalone():
            print(f"{'OK  ' if ok else 'FAIL'} {name} {err}")
    else:
        t0 = time.time()
        build()
        print(f"warehouse built in {time.time() - t0:.1f}s -> {get_settings().warehouse_path}")


if __name__ == "__main__":
    main()
