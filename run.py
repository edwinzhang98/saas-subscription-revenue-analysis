"""One command for the whole pipeline.

    python run.py            # everything, in order (same as `make all`)
    python run.py warehouse marts report      # selected steps

Steps
    generate    synthetic source tables -> <data_dir>/raw            (data_gen/generate.py)
    sanity      generator design-target checks -> reports/generator_sanity_checks.json
    warehouse   DuckDB warehouse: run sql/00..91 in order
    marts       parquet cache for the dashboard + every analysis module's tables and key numbers
    crosscheck  independent Python re-implementation of MRR / movements / NRR vs SQL
    validate    known-answer tests (planted effects vs recovered) + data-quality summary
    figures     static PNG charts for the README (reports/figures/)
    notebooks   execute notebooks/0*.ipynb in place and export HTML to reports/
    report      reports/report.html (standalone)
    docs        docs/monthly_revenue_review.md and docs/account_health_list.csv
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "data_gen"))

from saas_rev.config import get_settings  # noqa: E402

ANALYSES = ["datamodel", "metrics", "transitions", "expansion", "silent_churn", "recommendations"]
STEPS = ["generate", "sanity", "warehouse", "marts", "crosscheck", "validate", "figures", "notebooks", "report", "docs"]


def step_generate() -> None:
    import generate
    s = get_settings()
    m = generate.generate(s.raw_dir, s.seed, ROOT / "data_gen" / "params.yaml")
    print(f"    rows: {m['row_counts']}")


def step_sanity() -> None:
    import sanity_checks
    res = sanity_checks.run(get_settings().raw_dir)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "generator_sanity_checks.json").write_text(json.dumps(res, indent=2, default=str))
    n_pass = sum(r["pass"] for r in res["checks"])
    print(f"    {n_pass}/{len(res['checks'])} generator checks pass")
    if not res["all_pass"]:
        raise SystemExit("generator sanity checks failed — see reports/generator_sanity_checks.json")


def step_warehouse() -> None:
    from saas_rev import warehouse
    warehouse.build(verbose=False)
    print(f"    {len(warehouse.sql_files())} SQL files -> {get_settings().warehouse_path}")


def step_marts() -> None:
    from saas_rev import marts
    marts.export_warehouse()
    for name in ANALYSES:
        try:
            mod = importlib.import_module(f"saas_rev.analysis.{name}")
        except ModuleNotFoundError:
            print(f"    (skip {name}: module not present)")
            continue
        mod.build()
        marts.write_key_numbers(mod.TOPIC, mod.key_numbers())
        print(f"    {name}: tables + key numbers")


def step_crosscheck() -> None:
    from saas_rev import crosscheck
    res = crosscheck.run()
    out = ROOT / "reports" / "crosscheck_results.json"
    out.write_text(json.dumps(res, indent=2, default=str))
    print(f"    cross-check written to {out.relative_to(ROOT)}")


def step_validate() -> None:
    from saas_rev import validation
    validation.run()


def step_figures() -> None:
    from saas_rev import figures
    for p in figures.export_all():
        print(f"    {p.relative_to(ROOT)}")


def step_notebooks() -> None:
    out_dir = ROOT / "reports"
    for nb in sorted((ROOT / "notebooks").glob("0*.ipynb")):
        subprocess.run([sys.executable, "-m", "jupyter", "nbconvert", "--to", "notebook", "--execute", "--inplace",
                        "--ExecutePreprocessor.timeout=600", str(nb)], check=True, cwd=ROOT / "notebooks")
        subprocess.run([sys.executable, "-m", "jupyter", "nbconvert", "--to", "html", "--output-dir", str(out_dir),
                        str(nb)], check=True, cwd=ROOT / "notebooks")
        print(f"    {nb.name} -> reports/{nb.stem}.html")


def step_report() -> None:
    from saas_rev import report
    print(f"    {report.build().relative_to(ROOT)}")


def step_docs() -> None:
    from saas_rev import docs_gen
    for p in docs_gen.build_all():
        print(f"    {p.relative_to(ROOT)}")


def main(argv: list[str]) -> None:
    steps = STEPS if not argv or argv == ["all"] else argv
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        raise SystemExit(f"unknown step(s): {unknown}; choose from {STEPS}")
    t_all = time.time()
    for s in steps:
        t0 = time.time()
        print(f"[{s}]")
        globals()[f"step_{s}"]()
        print(f"    done in {time.time() - t0:.1f}s")
    print(f"pipeline finished in {time.time() - t_all:.1f}s")


if __name__ == "__main__":
    main(sys.argv[1:])
