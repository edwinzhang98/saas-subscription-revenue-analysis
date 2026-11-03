"""SQL layer: every file runs on its own; metric identities hold; data-quality checks pass."""
import pandas as pd


def test_every_sql_file_runs_standalone(built):
    from saas_rev import warehouse
    results = warehouse.check_standalone()
    failed = [(name, err) for name, ok, err in results if not ok]
    assert not failed, failed
    assert len(results) == len(warehouse.sql_files())


def test_waterfall_identity_to_the_cent(con):
    gap = con.execute("SELECT MAX(ABS(identity_gap)) FROM rpt_mrr_waterfall_monthly").fetchone()[0]
    assert gap < 0.005


def test_components_tie_out_to_expansion_minus_contraction(con):
    comp = con.execute("SELECT SUM(comp_plan + comp_seats + comp_billing_period + comp_discount + comp_other) "
                       "FROM fct_mrr_movement_components").fetchone()[0]
    net = con.execute("SELECT SUM(expansion_mrr) - SUM(contraction_mrr) FROM fct_mrr_movements").fetchone()[0]
    assert abs(comp - net) < 0.01


def test_nrr_matches_a_hand_calculation(con):
    """NRR at 2026-09-30 recomputed from the customer-month table with plain pandas."""
    f = con.execute("SELECT customer_id, month_end, mrr FROM fct_customer_mrr_monthly "
                    "WHERE month_end IN ('2025-09-30', '2026-09-30')").df()
    piv = f.pivot(index="customer_id", columns="month_end", values="mrr").fillna(0)
    base = piv[piv[pd.Timestamp("2025-09-30")] > 0]
    hand = base[pd.Timestamp("2026-09-30")].sum() / base[pd.Timestamp("2025-09-30")].sum()
    sql = con.execute("SELECT nrr FROM rpt_nrr_grr_ttm WHERE month_end = '2026-09-30'").fetchone()[0]
    assert abs(hand - sql) < 1e-12


def test_no_failed_data_quality_checks(con):
    dq = con.execute("SELECT check_id, check_name, observed FROM dq_check_results WHERE status = 'fail'").df()
    assert dq.empty, dq.to_dict("records")


def test_exactly_six_internal_accounts_excluded(con):
    assert con.execute("SELECT COUNT(*) FROM stg_customers WHERE is_internal").fetchone()[0] == 6


def test_billing_reconciles_within_tolerance(con):
    gaps = con.execute("SELECT period, gap_pct FROM dq_revenue_reconciliation WHERE period_type <> 'month'").df()
    assert (gaps["gap_pct"].abs() < 0.003).all(), gaps
