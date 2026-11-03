"""Build reports/report.html: one standalone HTML file (plotly.js inlined once, no server, no CDN).

Charts come from the same functions the dashboard and notebooks use, so the three always match.
Every number in the text is read from the marts / key-number files at build time.
"""
from __future__ import annotations

import html
import importlib
import json
from pathlib import Path

import pandas as pd
import plotly.io as pio
import plotly.offline
from jinja2 import Environment, FileSystemLoader

from . import marts, theme
from .config import REPO_ROOT, get_settings
from .theme import money, pct, pts

OUT = REPO_ROOT / "reports" / "report.html"
CONFIG = {"displaylogo": False, "responsive": True, "modeBarButtonsToRemove": ["lasso2d", "select2d"]}
_fig_counter = [0]


# =============================================================================== html helpers
def fig(f, note: str | None = None) -> str:
    _fig_counter[0] += 1
    f.update_layout(template=theme.template())
    div = pio.to_html(f, include_plotlyjs=False, full_html=False, config=CONFIG, default_width="100%",
                      div_id=f"fig{_fig_counter[0]}")
    return f"<div class='chart'>{div}</div>" + (f"<p class='read'>{note}</p>" if note else "")


def table(df: pd.DataFrame, formats: dict | None = None, num_cols: tuple = ()) -> str:
    formats = formats or {}
    head = "".join(f"<th class='{'num' if c in num_cols else ''}'>{html.escape(str(c))}</th>" for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            s = formats[c](v) if (c in formats and pd.notna(v)) else ("" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v))
            cells.append(f"<td class='{'num' if c in num_cols else ''}'>{s}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class='tablewrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"


def kpis(items: list[tuple[str, str, str]]) -> str:
    cells = "".join(f"<div class='kpi'><div class='label'>{a}</div><div class='value'>{b}</div><div class='delta'>{c}</div></div>"
                    for a, b, c in items)
    return f"<div class='kpis'>{cells}</div>"


def finding(text: str) -> str:
    return f"<div class='finding'>{text}</div>"


def section(sid: str, title: str, body: str, children: list[tuple[str, str]] | None = None) -> dict:
    return {"id": sid, "title": title, "html": body,
            "children": [{"id": c, "title": t} for c, t in (children or [])]}


ER_SVG = """
<div class='er'><svg viewBox='0 0 990 430' role='img' aria-label='Entity-relationship diagram of the six source tables'>
<style>.bx{fill:#fcfcfb;stroke:#c3c2b7}.hd{fill:#2a78d6}.hdg{fill:#52514e}.hdu{fill:#1baf7a}.t{font:13px system-ui,sans-serif;fill:#0b0b0b}
.th{font:600 13px system-ui,sans-serif;fill:#fff}.ln{stroke:#898781;stroke-width:1.5;fill:none}.lb{font:11px system-ui,sans-serif;fill:#52514e}</style>
<g><rect class='bx' x='30' y='150' width='230' height='132' rx='6'/><rect class='hd' x='30' y='150' width='230' height='26' rx='6'/>
<text class='th' x='42' y='168'>customers</text><text class='t' x='42' y='196'>customer_id (PK)</text><text class='t' x='42' y='216'>segment · industry · region</text>
<text class='t' x='42' y='236'>acquisition_channel</text><text class='t' x='42' y='256'>cs_coverage · csm_owner</text></g>
<g><rect class='bx' x='380' y='20' width='240' height='76' rx='6'/><rect class='hdg' x='380' y='20' width='240' height='26' rx='6'/>
<text class='th' x='392' y='38'>plans</text><text class='t' x='392' y='64'>plan_id (PK) · tier_rank</text><text class='t' x='392' y='84'>monthly / annual price per seat</text></g>
<g><rect class='bx' x='380' y='150' width='240' height='152' rx='6'/><rect class='hd' x='380' y='150' width='240' height='26' rx='6'/>
<text class='th' x='392' y='168'>subscriptions</text><text class='t' x='392' y='196'>subscription_id (PK)</text><text class='t' x='392' y='216'>customer_id (FK)</text>
<text class='t' x='392' y='236'>start_date · end_date (exclusive)</text><text class='t' x='392' y='256'>initial plan / seats / billing</text>
<text class='t' x='392' y='276'>status · cancel_reason</text></g>
<g><rect class='bx' x='740' y='20' width='230' height='152' rx='6'/><rect class='hd' x='740' y='20' width='230' height='26' rx='6'/>
<text class='th' x='752' y='38'>plan_changes</text><text class='t' x='752' y='66'>change_id (PK)</text><text class='t' x='752' y='86'>subscription_id (FK)</text>
<text class='t' x='752' y='106'>requested · effective date</text><text class='t' x='752' y='126'>from_* / to_* terms</text><text class='t' x='752' y='146'>change_type · change_source</text></g>
<g><rect class='bx' x='740' y='230' width='230' height='112' rx='6'/><rect class='hd' x='740' y='230' width='230' height='26' rx='6'/>
<text class='th' x='752' y='248'>invoices</text><text class='t' x='752' y='276'>invoice_line_id (PK)</text><text class='t' x='752' y='296'>recurring / proration / one_time</text>
<text class='t' x='752' y='316'>service period · amount · status</text></g>
<g><rect class='bx' x='30' y='330' width='230' height='92' rx='6'/><rect class='hdu' x='30' y='330' width='230' height='26' rx='6'/>
<text class='th' x='42' y='348'>usage_events</text><text class='t' x='42' y='376'>customer_id · week_start</text><text class='t' x='42' y='396'>event_name · count · users</text></g>
<path class='ln' d='M260 214 H380'/><text class='lb' x='300' y='206'>1 : n</text>
<path class='ln' d='M500 96 V150'/><text class='lb' x='508' y='128'>initial plan</text>
<path class='ln' d='M620 190 C680 190 680 96 740 96'/><text class='lb' x='660' y='140'>1 : n</text>
<path class='ln' d='M620 262 C680 262 680 286 740 286'/><text class='lb' x='664' y='258'>1 : n</text>
<path class='ln' d='M145 282 V330'/><text class='lb' x='152' y='312'>weekly</text>
</svg></div>
"""


# =============================================================================== sections (metrics, data, validation)
def _k(topic: str) -> dict:
    return marts.read_key_numbers(topic)


def s_summary() -> dict:
    k = _k("metrics")
    body = kpis([
        ("ARR at 30 Sep 2026", money(k["arr"]), f"{pct(k['mrr_yoy_growth'], 0, signed=True)} vs Sep 2025"),
        ("12-month NRR", pct(k["nrr_ttm"]), f"{pts(k['nrr_ttm'] - k['nrr_ttm_2025_09'])} vs Sep 2025 ({pts(k['nrr_ttm'] - k['nrr_ttm_2026_06'])} since June)"),
        ("12-month GRR", pct(k["grr_ttm"]), f"{pts(k['grr_ttm'] - k['grr_ttm_2025_09'])} vs Sep 2025"),
        ("Q3 expansion rate", pct(k["expansion_rate_q3_2026"]), f"{pts(k['expansion_rate_q3_2026'] - k['expansion_rate_q3_2025'])} vs Q3 2025"),
        ("Q3 run-rate NRR", pct(k["nrr_runrate_q3_2026"]), f"vs {pct(k['nrr_runrate_q3_2025'])} in Q3 2025"),
        ("Paying customers", f"{k['paying_customers']:,}", f"ARPA {money(k['arpa'], 0)}"),
    ])
    body += "<p class='lead'>The board asked for NRR and why expansion slowed last quarter. Short answer:</p>"
    drop = k["expansion_q2_2026"] - k["expansion_q3_2026"]
    body += finding(f"<b>Expansion did slow. Most of the drop from Q2 is a one-off, but the like-for-like rate fell too — and that part "
                    f"is structural.</b> Q3 expansion was {money(k['expansion_q3_2026'])}, {pct(k['expansion_change_qoq'], 0, signed=True)} vs Q2; "
                    f"{pct(k['one_off_share_of_q2'], 0)} of Q2's expansion was a one-off rollout at {k['one_off_company']} "
                    f"({pct(k['one_off_expansion_mrr'] / drop, 0)} of the {money(drop)} drop). Like for like, the expansion rate fell from "
                    f"{pct(k['expansion_rate_q2_2026_ex_one_off'])} (Q2) and {pct(k['expansion_rate_q3_2025'])} (Q3 2025) to "
                    f"{pct(k['expansion_rate_q3_2026'])} of starting MRR — more than the 1-point threshold the VP set for re-forecasting.")
    tr_ = _k("recommendations").get("tech_rates", {})
    t25, t26 = tr_.get("q3_2025", {}), tr_.get("q3_2026", {})
    tech_detail = (f"; their expansion fell from {pct(t25['expansion_rate'], 1)} to {pct(t26['expansion_rate'], 1)} of their own starting MRR "
                   f"a quarter and their contraction rose from {pct(t25['contraction_rate'], 1)} to {pct(t26['contraction_rate'], 1)}"
                   if t25 and t26 else "")
    body += finding(f"<b>Two drivers explain it:</b> Technology-sector accounts all but stopped adding seats and started cutting "
                    f"them ({money(abs(k['bridge_yoy']['Technology (all)']))} less expansion than their Q3 2025 rate{tech_detail}), and "
                    f"plan upgrades outside Technology — mostly Basic → Premium — brought {money(abs(k['bridge_yoy']['Non-Tech plan upgrades']))} "
                    f"less: the Basic upgrade rate was already falling before April and fell again after the packaging change. Seat expansion outside "
                    f"Technology is healthy ({money(k['bridge_yoy']['Non-Tech seat expansion'])} more).")
    body += finding(f"<b>The 12-month NRR of {pct(k['nrr_ttm'])} flatters the trend:</b> without the one-off it is {pct(k['nrr_ttm_ex_one_off'])}, "
                    f"and the Q3 run-rate annualises to {pct(k['nrr_runrate_q3_2026'])}. GRR is {pct(k['grr_ttm'])}: contraction nearly doubled "
                    f"({pct(k['contraction_rate_q3_2025'])} → {pct(k['contraction_rate_q3_2026'])} of starting MRR per quarter).")
    sc = _k("silent_churn")
    if sc:
        body += SUMMARY_EXTRA.get("silent", lambda sc: "")(sc)
    rec = _k("recommendations")
    if rec:
        body += SUMMARY_EXTRA.get("recs", lambda r: "")(rec)
    return section("summary", "Summary", body)


SUMMARY_EXTRA: dict = {}   # filled by the slice sections below when their results exist


def s_request() -> dict:
    body = """
<p><b>The request (VP of Revenue, 13 Oct 2026):</b> <i>"The board wants to see NRR. Expansion clearly slowed last quarter — why?
Which customers are quietly downgrading? Which accounts should the CS team prioritize?"</i></p>
<p>Clarified into six questions (full scope in <code>docs/01_request_and_scope.md</code>):</p>
""" + table(pd.DataFrame([
        ("Q1", "What are NRR and GRR, defined as the board pack will footnote them, and how have they trended?", "Metrics, NRR / GRR"),
        ("Q2", "How much did expansion slow in Q3 2026, and how much is one-off, seasonal, segment-specific, upgrades vs seats, customer mix?", "Expansion bridges"),
        ("Q3", "Which paying customers are shrinking without cancelling, how much ARR is that, and who are they?", "Silent churn"),
        ("Q4", "Can we see silent churn early enough for CS to act?", "Early-warning rule"),
        ("Q5", "Which Basic accounts show pre-upgrade behaviour, and what are the signals?", "Expansion signals"),
        ("Q6", "What should CS, Sales, Product and Marketing do, and what is it worth in NRR points?", "Recommendations"),
    ], columns=["#", "Question", "Section"])) + """
<p><b>Decision rules agreed up front.</b> VP: if the Q3 2026 expansion rate excluding the one-off rollout is within 0.5 pt of Q3 2025, the
board message is "timing, not trend"; if it is 1.0 pt or more below, re-forecast Q4 / FY2027 expansion and name each structural driver
with an owner. Head of CS: the top 120 accounts by ARR at stake go to CSMs (team capacity), everything else to a digital play.</p>
"""
    return section("request", "The request and how it was scoped", body)


def s_data() -> dict:
    rc = marts.load("datamodel__row_counts")
    body = f"""
<p>Five tables a Stripe / Chargebee-style billing system and a product-analytics export would produce, plus the plan catalog. MRR is
never stored: it is derived in SQL from each subscription's terms on a given day.</p>
{ER_SVG}
{table(rc.rename(columns={'table': 'Table', 'rows': 'Rows'}), {'Rows': lambda v: f'{int(v):,}'}, num_cols=('Rows',))}
<p><b>How the data is generated</b> (<code>docs/data_model.md</code>, <code>data_gen/params.yaml</code>). Each account is simulated week by week
with its own random stream (fixed seed): a hidden health score drifts and takes shocks; team size grows or shrinks with hiring and adoption;
seats follow team size (bought when the team outgrows them, cut at billing dates when there is surplus); usage comes from health and seats;
upgrades, downgrades, billing switches and cancellations are probability rules by plan, segment and channel. Monthly plans bill on the
anniversary day, annual plans upfront; increases are prorated, decreases wait for the period end; failed cards go through 14 days of dunning.
Seasonality, a Paid Social acquisition program, a Basic packaging change, a Technology-sector slowdown and a one-off enterprise rollout are
built in — see <a href='#validation'>method validation</a>.</p>
"""
    return section("data", "Data", body)


def s_definitions() -> dict:
    defs = pd.DataFrame([
        ("MRR", "Month-end snapshot: seats × monthly-equivalent price × (1 − discount); annual ÷ 12; excludes one-time fees, prorations, tax"),
        ("Movements", "Customer-level, month over month: new, expansion, contraction, churn, reactivation; start + movements = end, to the cent"),
        ("Components", "Each plan change split plan → seats → billing period → discount; billing switches are their own component"),
        ("NRR (TTM)", "Σ MRR today ÷ Σ MRR 12 months ago, for customers paying 12 months ago (point-to-point; churn counts as 0)"),
        ("GRR (TTM)", "Same base, each customer capped at its starting MRR (no expansion, ≤ 100%)"),
        ("Run-rate NRR", "Same calculation over one quarter, annualised (^4) — leading, noisier"),
        ("Expansion rate", "Quarter's expansion MRR ÷ MRR at the start of the quarter"),
        ("Silent contraction", "Paying, no cancellation requested, billing-neutral MRR ≥ 20% below its 6-month peak, with a seat / plan / discount cut"),
    ], columns=["Metric", "Definition (full rules in docs/02_metric_definitions.md)"])
    return section("definitions", "Metric definitions", table(defs))


def s_validation() -> dict:
    dq = marts.load("dq_check_results")
    rec = marts.load("dq_revenue_reconciliation")
    cc_path = REPO_ROOT / "reports" / "crosscheck_results.json"
    cc = json.loads(cc_path.read_text()) if cc_path.exists() else None
    ak = _k("answer_key")
    n_pass, n_fail, n_info = ((dq["status"] == s).sum() for s in ("pass", "fail", "info"))
    yr = rec[rec["period_type"] != "month"][["period", "billed_serviced", "daily_mrr_revenue", "gap_pct"]]
    core = [c for c in cc["comparisons"] if not c["informational"]] if cc else []
    body = f"""
<p><b>Data-quality checks:</b> {n_pass} pass, {n_fail} fail, {n_info} monitored (known issues found and handled: test accounts, duplicate
usage loads, orphan plan changes, duplicate CRM records). Full list in <code>docs/03_data_quality_checks.md</code>.</p>
<p><b>Billing reconciles with MRR</b> (invoices spread over their service periods vs daily MRR):</p>
{table(yr.rename(columns={'period': 'Period', 'billed_serviced': 'Billed recurring revenue', 'daily_mrr_revenue': 'Revenue implied by MRR', 'gap_pct': 'Gap'}),
       {'Billed recurring revenue': money, 'Revenue implied by MRR': money, 'Gap': lambda v: pct(v, 3)},
       num_cols=('Billed recurring revenue', 'Revenue implied by MRR', 'Gap'))}
<p><b>Two independent implementations agree.</b> A second analyst re-implemented MRR, movements, components, NRR / GRR and cohorts in Python
from the definitions document alone (without reading the SQL): {sum(c['status'] == 'match' for c in core)}/{len(core)} comparisons match to
the cent on {cc['comparisons'][0]['rows_compared']:,} customer-months. The two differences it found were gaps in the documentation, now fixed.</p>
""" if cc else ""
    if ak:
        rows = pd.DataFrame(ak["rows"])[["event", "planted", "recovered", "naive_cut", "verdict"]]
        body += """<h3 id='validation'>Method validation (known-answer tests)</h3>
<p>Because the data is synthetic, the planted effects are known. The table checks that the metric layer recovers each one — and shows what a
naive cut would have concluded. The analysis never reads the answer key; only the validation step does.</p>""" + table(
            rows.rename(columns={"event": "Planted effect", "planted": "Planted", "recovered": "Recovered", "naive_cut": "Naive cut",
                                 "verdict": "Verdict"}))
    return section("quality", "Data quality and method validation", body)


def s_mrr() -> dict:
    from .analysis import metrics as M
    k = _k("metrics")
    q = M.quarterly_base()
    body = fig(M.fig_mrr_trend(), "Growth is still mostly new logos: they added "
               f"{pct(q.loc['2026-Q3', 'new_rate'])} of starting MRR in Q3 2026 ({pct(q.loc['2025-Q3', 'new_rate'])} in Q3 2025).")
    c3 = q.loc["2026-Q3"]
    losses = c3["contraction_mrr"] + c3["churn_mrr"]
    gains = c3["expansion_mrr"] + c3["reactivation_mrr"]
    earlier = q.loc[[i for i in q.index if i < "2026-Q3"]]
    first_time = bool(((earlier["contraction_mrr"] + earlier["churn_mrr"]) <= (earlier["expansion_mrr"] + earlier["reactivation_mrr"])).all())
    body += fig(M.fig_monthly_movements(), f"<b>Reading:</b> bars above zero add MRR, below zero remove it; the black line is the net. "
                f"In Q3 2026 contraction ({money(c3['contraction_mrr'])}) and churn ({money(c3['churn_mrr'])}) came to {money(losses)} — "
                f"{'more than' if losses > gains else 'less than'} expansion + win-backs ({money(gains)})"
                + (", for the first time in the series" if losses > gains and first_time else "")
                + (f": the existing base lost {money(losses - gains)} net and only new logos kept MRR growing." if losses > gains else "."))
    body += fig(M.fig_quarter_waterfall("2026-Q3"))
    return section("mrr", "MRR and the monthly waterfall", body)


def _nrr_peak_sentence(k: dict) -> str:
    n = marts.load("rpt_nrr_grr_ttm")
    n = n[n["is_reportable"]]
    peak = n.loc[n["nrr"].idxmax()]
    return (f"NRR peaked in {pd.Timestamp(peak['month_end']):%B %Y} ({pct(peak['nrr'])}), the month of the one-off rollout; excluding "
            f"the rollout, NRR went from {pct(k['nrr_ttm_2026_06_ex_one_off'])} in June to {pct(k['nrr_ttm_ex_one_off'])} in September.")


def s_nrr() -> dict:
    from .analysis import metrics as M
    k = _k("metrics")
    body = fig(M.fig_nrr_trend(), f"<b>Reading:</b> the {k['nrr_base_customers']:,} customers paying on 30 Sep 2025 now pay {pct(k['nrr_ttm'])} "
               f"of what they paid then; capped at their starting MRR they keep {pct(k['grr_ttm'])}. Gray points rest on fewer than 1,000 "
               f"customers. " + _nrr_peak_sentence(k))
    body += fig(M.fig_nrr_runrate(), "Run-rate NRR reacts within a quarter but is seasonal and noisy — use it as the early warning that the "
                "12-month figure will keep falling, not as the board number.")
    return section("nrr", "NRR and GRR", body)


def s_expansion() -> dict:
    from .analysis import metrics as M
    k = _k("metrics")
    s = k["seasonality_q2_q3_change_pts"]
    body = """<p>Every expanding customer-month is attributed to one driver bucket — Technology accounts, or non-Tech by what grew most (seats
or plan) — so the steps add up exactly. Steps: remove the one-off rollout, allow for a bigger base, then each driver's change in
expansion rate × this quarter's starting MRR.</p>"""
    body += fig(M.fig_bridge("qoq"), f"<b>Sequential (the VP's framing):</b> the {money(k['one_off_expansion_mrr'])} rollout is the biggest "
                f"single step; after it, Technology accounts take away {money(abs(k['bridge_qoq']['Technology (all)']))}.")
    body += fig(M.fig_bridge("yoy"), f"<b>Year over year (same season — the primary view for structural drivers):</b> Technology "
                f"({money(k['bridge_yoy']['Technology (all)'])}) and non-Tech plan upgrades ({money(k['bridge_yoy']['Non-Tech plan upgrades'])}) "
                f"explain the fall; non-Tech seat expansion grew ({money(k['bridge_yoy']['Non-Tech seat expansion'])}).")
    body += fig(M.fig_expansion_rate_by_bucket(), f"Seasonality check (n = 2): the expansion rate moved {pts(s['2024'])} from Q2 to Q3 in 2024 "
                f"and {pts(s['2025'])} in 2025 — not enough years to separate a summer effect from noise, which is why the year-over-year "
                "bridge carries the conclusion.")
    return section("expansion", "Why expansion slowed in Q3 2026", body)


def s_segments() -> dict:
    from .analysis import metrics as M
    body = "<div class='grid2'>" + fig(M.fig_segment_dumbbell("industry")) + fig(M.fig_segment_dumbbell("acquisition_channel")) + "</div>"
    body += fig(M.fig_segment_dumbbell("segment"))
    from .docs_mbr import _cohort_sentence
    coh = marts.load("rpt_cohort_retention")
    body += fig(M.fig_cohort_heatmap("nrr", "quarter"), "<b>Reading the cohorts:</b> each row is the customers who first paid in that "
                "quarter; each column is months since then; blank cells are not observed yet for the whole cohort. "
                + _cohort_sentence(coh[coh["cohort_grain"] == "quarter"])
                + " The month-12 dip is the first annual renewal, where customers right-size seats or leave.")
    return section("segments", "Segments and cohorts", body)


def s_limitations() -> dict:
    body = """<ul>
<li><b>Synthetic data.</b> Effect sizes come from generator rules; the method, definitions and plays transfer, the magnitudes do not.</li>
<li><b>Month-end snapshots</b> miss customers who start and cancel inside one month and net several changes within a month (counted in the
data-quality checks).</li>
<li><b>Observational, not causal.</b> The fall in plan upgrades coincides with the April packaging change and with a lower-intent Paid Social
mix; the decomposition separates them as far as the data allows, and a holdout test is the clean answer.</li>
<li><b>Seasonality</b> rests on two prior years.</li>
<li><b>Expected-impact figures for the plays are assumptions</b> (reach × effect size) sized on our data, to be replaced by measured effects
from the holdouts proposed with each play.</li>
</ul>
<p><b>Data I would want next:</b> Sales pipeline and opportunity data for sales-assisted expansion; CRM notes and headcount signals for
Technology accounts; product-analytics event data at user level (feature adoption by role); a randomised holdout for any packaging or
pricing change.</p>"""
    return section("limitations", "Limitations and next steps", body)


def s_appendix() -> dict:
    nbs = sorted((REPO_ROOT / "reports").glob("0*_*.html"))
    links = "".join(f"<li><a href='{p.name}'>{p.stem.replace('_', ' ')}</a></li>" for p in nbs) or "<li>notebook exports in reports/</li>"
    body = f"""<p><b>Reproduce</b> (3 commands, ~2 minutes): <code>uv sync</code> → <code>make all</code> (or <code>uv run python run.py</code>) →
<code>uv run streamlit run dashboard/app.py</code>. Data goes to the folder set in <code>config.yaml</code> (override with
<code>SAAS_DATA_DIR</code>); nothing generated is committed except reports and documents.</p>
<p><b>Notebooks</b> (exported HTML, same folder as this report):</p><ul>{links}</ul>
<p><b>Where things are:</b> <code>sql/</code> metric layer (DuckDB) · <code>src/saas_rev/</code> analysis, charts, cross-check, validation ·
<code>dashboard/</code> Streamlit app · <code>docs/</code> scope, definitions, data quality, data model, monthly review, board readout,
CS account list.</p>"""
    return section("appendix", "Appendix: reproduce and navigate", body)


SECTION_ORDER = ["summary", "request", "data", "definitions", "quality", "mrr", "nrr", "expansion", "segments",
                 "transitions", "signals", "silent", "recommendations", "limitations", "appendix"]
SLICE_SECTIONS: dict = {}   # filled below by slice section builders


def build() -> Path:
    _fig_counter[0] = 0
    builders = {"summary": s_summary, "request": s_request, "data": s_data, "definitions": s_definitions, "quality": s_validation,
                "mrr": s_mrr, "nrr": s_nrr, "expansion": s_expansion, "segments": s_segments, "limitations": s_limitations,
                "appendix": s_appendix, **SLICE_SECTIONS}
    sections = [builders[s]() for s in SECTION_ORDER if s in builders]
    env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=False)
    page = env.get_template("report.html.j2").render(
        plotly_js=plotly.offline.get_plotlyjs(), sections=sections, as_of=get_settings().as_of_date.strftime("%d %b %Y"),
        generated_note=f"seed {get_settings().seed}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page)
    return OUT


# slice sections live in report_slices.py (added after the slice analyses) and register themselves
try:  # pragma: no cover
    importlib.import_module("saas_rev.report_slices")
except ModuleNotFoundError:
    pass
