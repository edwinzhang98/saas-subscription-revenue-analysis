"""Recommendations: the plays, who owns them, and what each is worth in NRR points (with adjustable assumptions)."""
import streamlit as st

from common import data_table, how_to_read, page_header, plot
from saas_rev.analysis import recommendations as R
from saas_rev.theme import money, pct


@st.cache_data(show_spinner=False)
def _plays():
    return R.build_plays()


@st.cache_data(show_spinner=False)
def _base_mrr():
    return R.forward_base_mrr()


page_header("Recommendations",
            "What should CS, Sales, Product and Marketing do next, and what is each play worth in NRR points?")

plays = _plays()
base_mrr = _base_mrr()

st.caption("Each play's size = addressable MRR (measured) × expected loss or opportunity (measured) × effect of the play "
           "(assumption, adjustable) × reach (share of the list the team works). Each account counts in one play only. Change the "
           "assumptions below; everything on this page is recalculated.")

overrides = {}
with st.expander("Adjust reach and effect assumptions", expanded=False, icon=":material/tune:"):
    for p in plays:
        c1, c2, c3 = st.columns([3, 2, 2])
        c1.markdown(f"**{p.id} · {p.title}**  \n<span class='note'>{p.effect_label}: low {pct(p.effect[0], 0)} · "
                    f"base {pct(p.effect[1], 0)} · high {pct(p.effect[2], 0)}</span>", unsafe_allow_html=True)
        reach = c2.slider("Reach", 0, 100, int(p.reach * 100), step=5, format="%d%%", key=f"reach_{p.id}") / 100
        eff = c3.slider("Effect", 0, int(max(p.effect[2] * 100 * 1.5, 10)), int(p.effect[1] * 100), step=1, format="%d%%",
                        key=f"eff_{p.id}") / 100
        overrides[p.id] = {"reach": reach, "effect": eff}

t = R.plays_table(plays, overrides)
total, lo, hi = t["nrr_pts"].sum(), t["nrr_pts_low"].sum(), t["nrr_pts_high"].sum()
c = st.columns(4)
c[0].metric("Expected NRR impact", f"+{total * 100:.1f} pts", f"range +{lo * 100:.1f} to +{hi * 100:.1f}", delta_color="off",
            delta_arrow="off", border=True, help="Next year's NRR base = MRR of today's paying customers")
c[1].metric("ARR protected or added", money(t["impact_mrr"].sum() * 12), "12-month run-rate", delta_color="off",
            delta_arrow="off", border=True)
c[2].metric("Plays", f"{len(t)}", "each with an owner and a holdout", delta_color="off", delta_arrow="off", border=True)
c[3].metric("NRR base", money(base_mrr * 12), "ARR of customers paying on 30 Sep 2026", delta_color="off",
            delta_arrow="off", border=True)

plot(R.fig_impact(t))

st.subheader("The plays")
for r in t.sort_values("nrr_pts", ascending=False).itertuples():
    with st.container(border=True):
        top = st.columns([4, 1])
        top[0].markdown(f"#### {r.id} · {r.play}\n**Owner:** {r.owner}")
        top[1].metric("NRR impact", f"+{r.nrr_pts * 100:.2f} pts", f"{money(r.impact_mrr * 12)} ARR", delta_color="off",
                      delta_arrow="off")
        st.markdown(f"**Who:** {r.who}  \n**What:** {r.what}  \n**Evidence:** {r.evidence}  \n"
                    f"**How we will know:** {r.measure}  \n**Risks:** {r.risk}")
        st.markdown(f"<span class='note'>Sizing: {r.sizing_text}. {r.sizing_note}</span>", unsafe_allow_html=True)

data_table(t[["id", "play", "owner", "accounts", "addressable_mrr", "outcome_rate", "effect_used", "reach", "impact_mrr",
              "nrr_pts", "nrr_pts_low", "nrr_pts_high"]], "Show the sizing table")

how_to_read("""
**What the numbers mean.** Each play has a target list that exists today (e.g. the CSM queue of the account health
list), an action, and an expected effect on next year's NRR. The expected effect is built from three parts:
1. **Addressable MRR** and **what usually happens to it** — both measured in our data (for example, the MRR of at-risk
   accounts and the share of it that similar accounts lost historically).
2. **The effect of the play** — an assumption (low / base / high), because these plays have not been run here yet. The
   ranges are judgement calls, set wide on purpose; they size and rank the plays, they do not promise results.
3. **Reach** — the share of the list the team actually works. The CSM desk is already capped at CS capacity
   (120 reviews a quarter), so its default reach is 100% of those 120.

**No double counting.** Every account is assigned to one play (CSM desk first, then Technology renewals, the digital
play, upgrade outreach, Paid Social), so the plays add up. Plays that act on new signups (the packaging test, most of the
Paid Social play) barely move next year's NRR — new customers are not in its base — and are sized only on what reaches
existing customers within 12 months.

**NRR points.** Impact in MRR divided by the MRR of today's paying customers (the base of next year's NRR). +1 point
means next year's NRR would be one percentage point higher if the play works as assumed.

**How to read the bar chart.** Each bar is the base case; the whisker spans the low and high assumption. Plays with long
bars and short whiskers are the safest bets; plays with wide whiskers need the holdout test before anyone counts on them.

**Why every play has a holdout.** The effect is an assumption until it is measured. Each play names a control group
(accounts not worked, a 50/50 test, a test cell of new signups) so that in one or two quarters the assumption is replaced
by a measured number — and the plays that do not work are stopped.
""")
