"""Four native destinations consuming validated, precomputed public evidence."""

import streamlit as st

from transitstress.artifacts import results_csv
from transitstress.config import app_mode
from transitstress.errors import TransitStressError
from transitstress.ui import local, reference
from transitstress.ui.bundle import DemoBundle, case_zip, counts
from transitstress.ui.charts import PIPELINES, baseline_context, folded, periodogram, timeline
from transitstress.ui.presentation import show_chart
from transitstress.ui.public_cache import public_bundle

LIMITATIONS = (
    "135 correlated injection cases across five evaluation stars, with paired processing "
    "by two detrending configurations. A convenience sample selected using diagnostic "
    "metadata and prior cache/basic-QC knowledge; no sector completeness or representative "
    "population claim. One mask realization; bundled period/phase; one injected duration; "
    "additive box model; upstream PDCSAP correction; injection after 10-minute binning. "
    "Propagated errors omit trend uncertainty and induced correlations. BLS/SNR, geometric "
    "recovery and retention are diagnostics, not planet confirmation, calibrated "
    "probabilities, survey completeness or population superiority."
)


def navigate(destination: str, content: str | None = None) -> None:
    st.session_state["destination"] = destination
    if content is not None:
        st.session_state["workbench_content"] = content


def safety_caption() -> None:
    st.warning(LIMITATIONS)


def recovery_cards(rows: list[dict]) -> None:
    for column, (pipe, label, _, _) in zip(st.columns(2), PIPELINES, strict=True):
        c = counts(rows, pipe)
        with column, st.container(border=True):
            st.subheader(label)
            st.metric("Exact / all selected injection cases", f"{c['exact']}/{c['planned']}")
            st.write(f"Exact / coverage eligible: {c['exact']}/{c['eligible']}")
            st.write(
                f"Insufficient coverage: {c['insufficient_coverage']} · "
                f"Technical failures: {c['technical_failures']}"
            )
            st.caption(
                f"Exact with / without / unknown matching background: "
                f"{c['exact_with_background']} / {c['exact_without_background']} / "
                f"{c['exact_background_unknown']}. "
                f"Matching backgrounds among selected injections: "
                f"{c['background_matches_all']}/{c['eligible']} eligible."
            )
    st.caption(
        "Geometric matches already present in the uninjected background must be "
        "qualified. A nonmatching background top peak does not prove causality or authenticity."
    )


def sanity_view(bundle: DemoBundle) -> None:
    sanity = bundle.sanity
    st.subheader("Known-signal sanity check · Pi Mensae c")
    st.write(
        f"Separate real known signal, TIC {sanity['tic_id']}, sector 1. "
        "No synthetic injection; excluded from the five evaluation stars."
    )
    st.write(
        f"Literature reference period: {sanity['reference']['period_days']:.5f} days "
        "(approximately 6.27 days). Broad independent search: 0.75–10 days."
    )
    st.dataframe(
        [
            dict(
                configuration=label,
                fitted_period_days=sanity["searches"][pipe]["period_days"],
                depth_snr=sanity["searches"][pipe]["depth_snr"],
                within_one_percent=sanity["post_search_comparison"][pipe]["within_one_percent"],
            )
            for pipe, label, _, _ in PIPELINES
        ],
        hide_index=True,
        width="stretch",
    )
    st.caption(
        f"Precomputed {sanity['verified_utc']}. This checks the pipeline on a "
        "documented signal; it is not discovery or confirmation. Literature truth "
        "was compared only after both blind searches."
    )
    with st.expander("Known-signal saved plots and source evidence"):
        st.image(bundle.content["sanity.png"], width="stretch")
        st.caption(
            "Original reference metadata retains its pre-search note. The completed "
            "sanity searches and post-search comparison above are the measured evidence."
        )
        st.json(sanity)
        st.download_button(
            "Download known-signal evidence JSON",
            bundle.content["sanity.json"],
            file_name="known-signal-sanity.json",
            mime="application/json",
            on_click="ignore",
        )
    st.markdown(
        "Reference: [Gandolfi et al. (2018), Table 5](https://arxiv.org/pdf/1809.07573v2). "
        "The paper's analysis differs from this downstream PDCSAP experiment."
    )


def start(bundle: DemoBundle, mode: str) -> None:
    st.title("Does the dip survive?")
    st.write(
        "How do two processing configurations and missing observations affect recovery "
        "of an artificial transit signal?"
    )
    st.write(
        "A light curve records a star's brightness over time. Removing slow brightness "
        "changes can also weaken a small dip. Keeping its depth and finding its timing "
        "both matter when looking for repeated dimming."
    )
    st.button("Explore a worked example", type="primary", on_click=open_example)
    st.button("Browse benchmark", on_click=navigate, args=("Benchmark",))
    st.divider()
    st.subheader("One experiment, four steps")
    st.markdown(
        "1. **Begin with real telescope measurements.** Use prepared TESS brightness data.\n"
        "2. **Insert an artificial signal.** Add a known, repeated box-shaped dimming.\n"
        "3. **Process it two ways.** Compare A / 0.5-day and B / 1.5-day detrending windows.\n"
        "4. **Compare recovery and depth.** Score the saved blind searches afterward."
    )
    st.caption(
        "A processing window is the full moving time interval used to estimate slow "
        "variation. Only that window differs between these two configurations."
    )
    st.info(
        "Start with the saved disagreement example: B keeps more artificial depth, "
        "while A recovers the intended period and timing. Selected after analysis to "
        "explain this distinction, not to represent every case."
    )
    st.caption(
        "Offline saved evidence: no upload or fresh computation needed. Five evaluation "
        "stars, 135 correlated injection cases, two detrending configurations, paired "
        "processing. Artificial signals are not planet discoveries."
    )
    with st.expander("Methods, advanced local analysis and separate sanity check"):
        st.button("Read methods", on_click=navigate, args=("Methods",))
        if mode == "local":
            st.button(
                "Open advanced local workbench",
                on_click=navigate,
                args=("Workbench", "Local experiment"),
            )
        else:
            st.write(
                "Advanced local analysis requires a separate local-mode launch on your own "
                "computer. Demo mode denies uploads and computation. Workbench provides "
                "launch instructions and saved examples that work here immediately."
            )
        sanity_view(bundle)


def open_example() -> None:
    """Change presentation state only; preserve private local drafts."""
    navigate("Workbench", "Curated examples")
    st.session_state["guided_case"] = next(
        c["case_id"] for c in public_bundle().manifest["curated"] if c["stem"] == "example3"
    )
    change_step(STEPS[0])
    st.session_state.pop("curated_case_widget", None)


def summary_table(rows: list[dict], detailed: set[str]) -> list[dict]:
    """Flatten saved values/reasons only; CSV remains the original typed schema."""
    output = []
    for row in rows:
        leading = (
            "case_id",
            "curve_id",
            "pipeline_id",
            "case_kind",
            "mask_name",
            "status",
            "scientific_outcome",
            "true_period_days",
            "true_depth",
            "period_days",
            "coverage_eligible",
            "exact_recovery",
        )
        record = {k: row[k] for k in leading}
        record.update(
            {k: v for k, v in row.items() if type(v) in (str, int, float, bool) or v is None}
        )
        for key, value in row.items():
            if type(value) is dict and set(value) == {"value", "reason"}:
                record[key] = value["value"]
                record[key + "_reason"] = value["reason"]
        record["evidence"] = "Detailed" if row["case_id"] in detailed else "Summary only"
        # Nullable uint64 seeds become rounded floats in native tables.
        # Display this identifier as text; typed scientific downloads stay unchanged.
        record["seed"] = None if row["seed"] is None else str(row["seed"])
        output.append(record)
    return output


def tic_id(row: dict) -> str:
    """Presentation label from the archived stable curve identifier."""
    return row["curve_id"].split("-", 1)[0].removeprefix("tic")


STEPS = [
    "1 · Observation",
    "2 · Artificial signal",
    "3 · Compare",
    "4 · Interpret",
    "5 · Evidence",
]


def remember_step() -> None:
    st.session_state["reading_step"] = st.session_state["reading_choice"]


def change_step(step: str) -> None:
    st.session_state["reading_step"] = step
    st.session_state["reading_choice"] = step


def remember_case() -> None:
    st.session_state["guided_case"] = st.session_state["curated_case_widget"]
    change_step(STEPS[0])


def remember_display(cid: str) -> None:
    # Conditional widgets expire; retain their last per-case preference while hidden.
    saved = st.session_state.setdefault("saved_display", {}).setdefault(cid, {})
    saved.update(
        {
            key: st.session_state[key]
            for key in ("public_time_range", "public_view", "public_layout")
            if key in st.session_state
        }
    )


def saved_chart_controls(cid: str, arrays: dict, bounds: list) -> None:
    saved = st.session_state.setdefault("saved_display", {}).get(cid, {})
    if st.session_state.get("public_display_case") != cid:
        for key in ("public_time_range", "public_view", "public_layout"):
            st.session_state.pop(key, None)
        st.session_state["public_display_case"] = cid
    for key, default in (
        ("public_time_range", tuple(bounds)),
        ("public_view", "Processed flux"),
        ("public_layout", "Stacked A/B"),
    ):
        if key not in st.session_state:
            st.session_state[key] = saved.get(key, default)
    view = st.radio(
        "Timeline view",
        ["Processed flux", "Prepared & injected", "Fitted trends"],
        horizontal=True,
        key="public_view",
        on_change=remember_display,
        args=(cid,),
    )
    zoom = st.slider(
        "Displayed time range (relative days)",
        float(bounds[0]),
        float(bounds[1]),
        value=None,
        step=0.01,
        format="%.2f days",
        key="public_time_range",
        on_change=remember_display,
        args=(cid,),
    )
    st.button("Reset to full baseline", on_click=_reset_public, args=(tuple(bounds), cid))
    layout = (
        st.radio(
            "Processed-flux layout",
            ["Stacked A/B", "Overlay"],
            horizontal=True,
            key="public_layout",
            on_change=remember_display,
            args=(cid,),
        )
        if view == "Processed flux"
        else "Stacked A/B"
    )
    if zoom[0] >= zoom[1]:
        st.warning("Displayed time range: choose an end later than the start, or reset.")
        return
    st.caption(
        "Full-baseline context: every retained injected-input dot; yellow shading marks "
        "the displayed interval below. Blank intervals have no observations."
    )
    show_chart(
        baseline_context(arrays, bounds, zoom),
        width="stretch",
        config={"displayModeBar": False},
    )
    if view == "Processed flux":
        st.write(
            "**Read the comparison.** Each dot is a processed brightness measurement. "
            "A uses white circles; B uses yellow diamonds. Axes show time in days and "
            "dimensionless normalized flux near 1. Both panels use the same fixed scale "
            "and aligned gaps."
        )
        st.caption(
            "Look for repeated dips amid background variation. Dots alone do not establish "
            "recovery; Interpret explains the saved period/timing result."
        )
    elif view == "Prepared & injected":
        st.write(
            "White open circles show the prepared telescope background; yellow dots show "
            "the same timestamps after artificial dimming. Axes show days and normalized "
            "flux (relative brightness). The injected difference may be subtle at this scale."
        )
    else:
        st.write(
            "Yellow dots show injected input. White solid A and yellow dashed B lines show "
            "saved moving-median trends. Axes show days and normalized flux. Notice their "
            "different responses to brightness changes; lines stop at segment gaps. "
            "A trend estimates slow variation, not the star's physical behavior."
        )
    show_chart(
        timeline(arrays, view, list(zoom), layout),
        width="stretch",
        config={"displayModeBar": False},
    )
    st.caption(
        f"All {len(arrays['time_days']):,} samples per configuration. Display-only zoom; "
        "matched time and fixed full-extrema flux limits; aligned gaps. "
        "No smoothing, averaging, clipping or downsampling."
    )


def detail(bundle: DemoBundle, cid: str) -> None:
    rows = [r for r in bundle.rows if r["case_id"] == cid]
    row = rows[0]
    if cid not in bundle.arrays:
        st.info(
            "Summary-only row: detailed evidence unavailable in this demo. Download the "
            "saved pair below. Full arrays require the matching original validated run; "
            "an exploratory rerun cannot replace frozen evidence."
        )
    else:
        selected = next(c for c in bundle.manifest["curated"] if c["case_id"] == cid)
        st.caption(f"Saved evaluation · TIC {tic_id(row)} · artificial injection")
        if "reading_choice" not in st.session_state:
            st.session_state["reading_choice"] = st.session_state.get("reading_step", STEPS[0])
        step = st.radio(
            "Reading guide",
            STEPS,
            horizontal=True,
            key="reading_choice",
            on_change=remember_step,
        )
        arrays, bounds = bundle.arrays[cid], row["original_bounds_days"]
        if step == STEPS[0]:
            st.subheader("Begin with a star's brightness")
            st.write(
                "Real TESS observations, prepared with PDCSAP correction, normalization "
                "and 10-minute binning; these are not raw telescope pixels."
            )
            st.write(
                "**Normalized flux** is brightness divided by a reference level: 1 is that "
                "level; 0.999 is 0.1% fainter. Each white open circle is a retained 10-minute "
                "measurement. Time is in days from the recorded starting epoch."
            )
            figure = timeline(arrays, "Prepared & injected", bounds)
            figure.data = (figure.data[0],)
            show_chart(figure, width="stretch", config={"displayModeBar": False})
            st.write(
                f"**Notice the missing observations.** {row['n_observed']:,} of "
                f"{row['n_original']:,} prepared bins remain after the "
                f"{row['mask_name']} mask. Blank intervals are gaps, not interpolated data. "
                "For central_block, the middle 20% of elapsed time is removed; random_missing "
                "removes 20% of bins, while original retains the prepared timestamps. "
                "This is the masked background; no separate pre-mask timeline is packaged."
            )
            st.caption(
                "Natural and instrumental variation can resemble signals. This background "
                "is not a verified planet-free observation."
            )
        elif step == STEPS[1]:
            st.subheader("Add a known artificial dimming")
            st.write(
                f"The saved experiment subtracts a box-shaped dip of "
                f"**{row['true_depth'] * 1e6:g} ppm** every "
                f"**{row['true_period_days']:g} days**, lasting "
                f"**{row['true_duration_days'] * 24:g} hours**."
            )
            st.write(
                "**ppm** means parts per million of reference brightness; 1,000 ppm is "
                "a 0.1% dip. **Period** is the time between repeated dips. These are artificial "
                "injection settings, not a measured planet. A transit is a brief dimming "
                "as something passes in front of a star."
            )
            st.write(
                "White open circles show the prepared background; yellow dots show the "
                "same observations with artificial dimming. Axes show days and "
                "dimensionless normalized flux. Notice the shared gaps: adding a signal "
                "does not create missing observations."
            )
            show_chart(
                timeline(arrays, "Prepared & injected", bounds),
                width="stretch",
                config={"displayModeBar": False},
            )
            st.caption(
                f"{row['observed_transits']}/{len(row['full_centers_days'])} full artificial "
                f"events sufficiently observed; {row['total_in_transit_points']} in-transit "
                "points. The dip may be subtle at this honest full flux scale. Its known "
                "timing was used only after both blind searches."
            )
        elif step == STEPS[2]:
            st.subheader("Compare two processing windows")
            st.write(
                "**A / 0.5-day detrending window** uses a shorter moving interval; "
                "**B / 1.5-day detrending window** uses a longer interval of the same "
                "rolling-median method. Window length is the full span around each "
                "measurement used to estimate slow variation, different from the period "
                "between artificial dips."
            )
            saved_chart_controls(cid, arrays, bounds)
        elif step == STEPS[3]:
            st.subheader("Depth and recovery answer different questions")
            st.write(
                "**Recovery** checks whether the strongest search peak matches both "
                "artificial period and timing. **Paired retained depth** measures the "
                "incremental dimming left after processing injected and uninjected data "
                "on identical timestamps. Retention ratio = retained / injected depth. "
                "Depth is in ppm (parts per million); fitted period is in days."
            )
            reference.result_cards(rows)
            if selected["stem"] == "example3":
                st.write(
                    "**What happened here:** the central block removes the event near day "
                    "11.6793; three full events remain sufficiently observed. A matches the "
                    "injected timing. B retains more incremental depth but its strongest peak "
                    "is elsewhere. Its uninjected background already peaks at that other "
                    "period, consistent with background structure outranking the injection."
                )
            elif selected["stem"] == "example2":
                st.write(
                    "Both strongest peaks miss the injected period/timing despite eligible "
                    "coverage. Selected after analysis for the smallest eligible saved "
                    "retention ratio, not as a typical outcome."
                )
            else:
                st.write(
                    "Both configurations match the injected period and timing in this "
                    "selected success. One success does not describe every case."
                )
            st.warning(
                "One selected case cannot establish general configuration superiority or "
                "identify a peak's physical origin. BLS and geometric matches are diagnostics, "
                "not planet confirmation or calibrated probabilities. A nonmatching "
                "background top peak does not prove causality."
            )
            with st.expander("Exact recovery and coverage criteria"):
                st.write(
                    "After blind search: period error ≤1% AND fitted-center error ≤half "
                    "the injected duration at every sufficiently observed true center. "
                    "Coverage: ≥2 full events with ≥3 points each and ≥6 in-transit points "
                    "overall. Half/double-period peaks stay separate."
                )
        else:
            st.subheader("Inspect the saved evidence")
            st.write(
                "Download the complete A/B pair, numeric arrays, units, settings and "
                "provenance. These are historical saved results; display changes and "
                "downloads do not rerun the experiment."
            )
            with st.expander("Broad BLS and fitted-period folds"):
                st.write(
                    "The periodogram shows saved search likelihood power over trial periods "
                    "in days. A is white solid; B yellow dashed. Yellow dotted vertical line: "
                    "injected period, annotated after search. Notice which peak wins. "
                    "Power is not planet probability."
                )
                show_chart(
                    periodogram(arrays, row["true_period_days"]),
                    width="stretch",
                    config={"displayModeBar": False},
                )
                st.write(
                    "The fold wraps every processed point around its fitted period. White "
                    "A circles and yellow B crosses share a flux axis; horizontal values "
                    "are days from each fitted center. A coherent-looking fold alone "
                    "does not establish an authentic transit."
                )
                show_chart(folded(arrays, rows), width="stretch", config={"displayModeBar": False})
        index = STEPS.index(step)
        left, right = st.columns(2)
        left.button(
            "← Previous",
            disabled=index == 0,
            on_click=change_step,
            args=(STEPS[max(0, index - 1)],),
            width="stretch",
        )
        right.button(
            "Next →" if index < 4 else "Back to observation",
            on_click=change_step,
            args=(STEPS[index + 1] if index < 4 else STEPS[0],),
            width="stretch",
        )
    with st.expander("Exact saved values, case ID, null reasons and units"):
        st.text(f"Case ID: {cid}")
        if cid in bundle.arrays:
            st.caption("Selection rule: " + "; ".join(selected["roles"]))
        st.json(rows)
    if cid not in bundle.arrays or st.session_state.get("reading_choice") == STEPS[4]:
        st.download_button(
            "Download selected pair evidence ZIP",
            case_zip(bundle, cid),
            file_name=f"frozen-case-{cid[:16]}.zip",
            mime="application/zip",
            on_click="ignore",
        )


def _reset_public(bounds: tuple[float, float], cid: str) -> None:
    st.session_state["public_time_range"] = bounds
    remember_display(cid)


def curated_workbench(bundle: DemoBundle, mode: str = "demo") -> None:
    st.title("Worked example")
    st.info(
        "Demo mode denies uploads and new computation. Saved evidence only."
        if mode == "demo"
        else "Saved evidence only. Advanced local experiments are separate in the sidebar."
    )
    examples = bundle.manifest["curated"]
    default = next(c["case_id"] for c in examples if c["stem"] == "example3")
    if "curated_case_widget" not in st.session_state:
        st.session_state["curated_case_widget"] = st.session_state.get("guided_case", default)
    cid = st.selectbox(
        "Curated detailed case",
        [c["case_id"] for c in examples],
        key="curated_case_widget",
        on_change=remember_case,
        format_func=lambda value: next(
            {
                "example1": "Selected success",
                "example2": "Lowest eligible retention",
                "example3": "Recovery disagreement · start here",
            }[c["stem"]]
            for c in examples
            if c["case_id"] == value
        ),
    )
    st.session_state["guided_case"] = cid
    st.caption(
        "Three selected cases with full saved evidence. Each illustrates one outcome; "
        "none represents all 135 correlated injection cases."
    )
    detail(bundle, cid)
    with st.expander("Advanced local workbench · availability and launch"):
        st.write(
            "Local mode adds private CSV input and a bounded exploratory experiment with "
            "an explicit Run comparison button. Use your own loopback server and locked "
            "environment. No Run or upload is available in this demo."
        )
        st.code(
            "TRANSITSTRESS_MODE=local uv run --locked --offline --no-sync "
            "streamlit run app.py --server.address 127.0.0.1",
            language="bash",
        )
    st.button("Browse all benchmark results", on_click=navigate, args=("Benchmark",))


def remember_filters() -> None:
    st.session_state["benchmark_filters"] = {
        key: st.session_state[key]
        for key in (
            "filter_star",
            "filter_mask",
            "filter_depth",
            "filter_family",
            "filter_kind",
            "filter_outcome",
            "filter_status",
        )
        if key in st.session_state
    }


def benchmark(bundle: DemoBundle) -> None:
    st.title("Benchmark · frozen evaluation")
    st.write(
        f"{bundle.manifest['protocol']} · completed {bundle.manifest['finished_utc']} · "
        "precomputed, validated bundle. 195 paired cases / 390 result rows: "
        "270 injection, 30 uninjected-real, 90 Gaussian-null. "
        f"Technical failure rows: {sum(r['status'] != 'ok' for r in bundle.rows)}; "
        "missing planned rows: 0. Scientific misses remain included."
    )
    st.caption(
        "Full frozen injection result: A 79/135; B 74/135. Unconditional and eligible "
        "denominators are both 135 per configuration; coverage exclusions 0. "
        "Filters below change the selected denominator, never the frozen totals."
    )
    st.write(
        "**Read the denominators first.** Each of 135 artificial injection cases is "
        "processed twice, giving 270 injection rows. Thirty real-background and 90 "
        "Gaussian-null rows bring the table to 390. Cases reuse five stars and are "
        "correlated. Exact means period AND timing agreement, not depth retention."
    )
    st.caption(
        "ppm = parts per million of reference brightness; period = days between "
        "artificial dips. Missingness removes observations. A and B use full "
        "processing windows of 0.5 and 1.5 days."
    )
    for key, value in st.session_state.get("benchmark_filters", {}).items():
        if key not in st.session_state:
            st.session_state[key] = value
    st.subheader("Choose a subset")
    stars = sorted({tic_id(r) for r in bundle.rows})
    filters = st.columns(2)
    with filters[0]:
        star = st.selectbox(
            "Evaluation star (TIC)", ["All", *stars], key="filter_star", on_change=remember_filters
        )
    with filters[1]:
        mask = st.selectbox(
            "Missingness",
            ["All", "original", "random_missing", "central_block"],
            key="filter_mask",
            on_change=remember_filters,
        )
    with filters[0]:
        depth = st.selectbox(
            "Injected depth (ppm)",
            ["All", 250, 750, 2250],
            key="filter_depth",
            on_change=remember_filters,
        )
    with filters[1]:
        family = st.selectbox(
            "Injected family period (days)",
            ["All", 1.73, 3.41, 6.83],
            key="filter_family",
            on_change=remember_filters,
        )
    base = [
        r
        for r in bundle.rows
        if (star == "All" or tic_id(r) == star)
        and (mask == "All" or r["mask_name"] == mask)
        and (depth == "All" or r["true_depth"] == depth * 1e-6)
        and (family == "All" or r["true_period_days"] == family)
    ]
    recovery_cards(base)
    st.caption(
        "Cards use selected injection cases before table-only outcome/kind filters. "
        "Depth/period filters omit controls because controls have no injection truth."
    )
    with st.expander("Table-only filters · kind, outcome and processing status"):
        kind = st.selectbox(
            "Table case kind",
            ["All", "injection", "uninjected_real", "gaussian_null"],
            key="filter_kind",
            on_change=remember_filters,
        )
        outcome = st.selectbox(
            "Table outcome",
            [
                "All",
                "exact",
                "other_peak",
                "half_period_peak",
                "double_period_peak",
                "insufficient_coverage",
                "not_applicable",
            ],
            key="filter_outcome",
            on_change=remember_filters,
        )
        status = st.selectbox(
            "Table processing status",
            ["All", "ok", "invalid_input", "detrend_error", "search_error", "artifact_error"],
            key="filter_status",
            on_change=remember_filters,
        )
    shown = [
        r
        for r in base
        if (kind == "All" or r["case_kind"] == kind)
        and (outcome == "All" or r["scientific_outcome"] == outcome)
        and (status == "All" or r["status"] == status)
    ]
    st.write(
        f"Showing {len(shown)} / {len(base)} selected rows / 390 frozen rows; "
        f"{len({r['case_id'] for r in shown})} distinct displayed cases. "
        "Table-only filters select outcomes; they are not a recovery-rate estimate."
    )
    st.caption(
        "Table/export units: time, duration and epoch in days; flux and depth are "
        "dimensionless relative flux; retention is a ratio; BLS power is likelihood. "
        "Cards/controls labelled ppm use depth × 1,000,000. Nulls retain reasons in JSON."
    )
    st.subheader("Inspect results and evidence")
    if shown:
        with st.expander("Full selected result table · all columns and IDs"):
            st.dataframe(summary_table(shown, set(bundle.arrays)), hide_index=True, width="stretch")
        st.download_button(
            "Download filtered results CSV",
            results_csv(shown),
            file_name="frozen-filtered-results.csv",
            mime="text/csv",
            on_click="ignore",
        )
        cid = st.selectbox(
            "Inspect displayed case", list(dict.fromkeys(r["case_id"] for r in shown))
        )
        st.caption(
            "Selected evidence always includes the full A/B pair, even if a table-only "
            "outcome filter showed one row."
        )
        detail(bundle, cid)
    else:
        st.info(
            "No rows match these filters. Select All to restore the full denominator; "
            "an empty selection is not zero recovery."
        )
    with st.expander("Frozen depth × missingness and per-star aggregate tables"):
        st.write(
            "These are complete frozen tables; they do not inherit the filters above. "
            "Depth in these original tables is dimensionless relative flux. "
            "The group column retains original curve IDs / depth/mask labels."
        )
        for name in (
            "group_counts.csv",
            "star_depth_mask.csv",
            "paired_summary.csv",
            "sampling.csv",
        ):
            st.write(name)
            st.dataframe(bundle.table(name), hide_index=True, width="stretch")
            st.download_button(
                "Download " + name,
                bundle.content[name],
                file_name=name,
                mime="text/csv",
                on_click="ignore",
            )
    with st.expander("Separate noise/control diagnostics"):
        st.write(
            "30 real-background rows and 90 Gaussian-null rows have no injected truth "
            "or recovery label. Real backgrounds are not verified negatives; Gaussian "
            "nulls omit correlated noise. A highest peak is not a false-alarm decision "
            "or a real-sky false-positive rate."
        )
        st.dataframe(bundle.analysis["controls"], hide_index=True, width="stretch")
        st.download_button(
            "Download control diagnostics CSV",
            bundle.content["control_diagnostics.csv"],
            file_name="control_diagnostics.csv",
            mime="text/csv",
            on_click="ignore",
        )
    safety_caption()


def methods(bundle: DemoBundle) -> None:
    st.title("Methods and provenance")
    st.write(
        "Pinned TESS PDCSAP → quality selection and normalization → occupied 10-minute "
        "weighted bins → synthetic injection → observation mask → independent A/B "
        "rolling medians → blind broad BLS → post-search evaluation."
    )
    st.write(
        "Time is in relative days; absolute BJD(TDB) epoch and original electron/second "
        "units remain metadata. Flux and error share one normalization factor. "
        "750 ppm is a 0.000750 relative-flux decrement, not a planet-size estimate."
    )
    st.write(
        "A uses a 0.5-day window; B a 1.5-day window of the same centered rolling median. "
        "Segments break at gaps >0.5 days. No interpolation, downward clipping, "
        "truth-aware detrending mask or transit masking. Central block removes 20% of "
        "elapsed baseline; random missing removes exactly floor(0.20 × N) bins."
    )
    st.write(
        "Independent BLS: 0.75–10 days; durations 1, 2, 3, 4, 6 hours; likelihood / fast; "
        "oversample 10; frequency factor 1. Original-baseline grid reused across "
        "masks and configurations. Injection truth never guides either blind stage."
    )
    with st.expander("Precise injection, recovery, coverage and retention definitions"):
        st.code(
            "r(t) = ((t - t0 + P/2) mod P) - P/2\n"
            "I(t) = (-D/2 <= r(t) < D/2)\nf_injected = f_background - depth * I(t)\n"
            "retained_depth = mean((z0-z1)[I]) - mean((z0-z1)[not I])\n"
            "retention_ratio = retained_depth / injected_depth",
            language=None,
        )
        st.write(
            "Exact recovery requires period error ≤1% AND fitted-center error ≤D_true/2 "
            "at every sufficiently observed true center. Coverage requires ≥2 full "
            "events with ≥3 samples each and ≥6 total in-transit samples. Half/double "
            "period peaks are separate labels; out-of-range aliases are unsearchable. "
            "Failures and insufficient coverage remain in unconditional denominators. "
            "Null measurements carry reasons; retention is never clamped to [0,1]."
        )
    safety_caption()
    with st.expander("Separate real-signal sanity check · Pi Mensae c"):
        sanity_view(bundle)
    identity = bundle.identity["identity"]
    st.subheader("Artifact and software provenance")
    with st.expander("Protocol, source and bundle fingerprints"):
        st.text(
            f"Protocol: {bundle.manifest['protocol']}\n"
            f"Run SHA-256: {bundle.manifest['run_id']}\n"
            f"Exact cohort SHA-256: {identity['data']['manifest']['cohort_hash']}\n"
            f"Scientific source SHA-256: {identity['scientific_source_hash']}\n"
            f"Lock SHA-256: {identity['lock_hash']}\nBundle SHA-256: {bundle.fingerprint}\n"
            f"Original run completed: {bundle.manifest['finished_utc']}\n"
            f"Presentation packaged: {bundle.manifest['packaged_utc']}"
        )
        st.caption(
            "Scientific identity uses source/data/settings/dependencies, not whole-repository "
            "HEAD. Later UI/docs commits do not change the frozen run. Full bundle "
            "checksums and all selected numeric payloads are validated before navigation. "
            "Hashes detect corruption; they do not authenticate an untrusted checkout."
        )
    with st.expander("Exact products, epochs, dependencies and producing commit"):
        st.json(bundle.identity)
    st.download_button(
        "Download full frozen results CSV",
        bundle.content["results.csv"],
        file_name="frozen-results.csv",
        mime="text/csv",
        on_click="ignore",
    )
    st.download_button(
        "Download artifact provenance JSON",
        bundle.content["identity.json"],
        file_name="frozen-identity.json",
        mime="application/json",
        on_click="ignore",
    )
    st.markdown(
        "Data: [TESS / MAST](https://archive.stsci.edu/missions-and-data/tess). "
        "Methods: [Astropy BoxLeastSquares]"
        "(https://docs.astropy.org/en/stable/timeseries/bls.html), "
        "[Lightkurve](https://docs.lightkurve.org/). Libraries and data retain their "
        "own licenses; original project implementation uses MIT."
    )
    st.write(
        "AI assistance: implementation, explanations and builder checks were prepared "
        "with coding-assistant help. Author learning and independent reviews are "
        "separate evidence; no author understanding is inferred. T07 results-only review "
        "is preserved. Historical combined Linux G3 is accepted; this presentation revision "
        "awaits separate review and author visual feedback. G4 remains pending."
    )
    st.subheader("Offline inspection and local reproduction")
    st.code(
        "uv run --locked python -m transitstress.ui.bundle\n"
        "uv run --locked python scripts/check.py --level ui\n"
        "uv run --locked python -m transitstress validate-artifact "
        "--path runs/t07_evaluation_20261020T1857Z",
        language="powershell",
    )
    st.caption(
        "The demo is self-contained. The last command requires the original run restored "
        "locally. Scoped assistant-performed V5 reproduction is recorded separately; "
        "native Windows proof remains unavailable. Dependency findings remain unresolved, "
        "with remediation/independent review pending. Privacy holds remain. No release "
        "or deployment is approved. Browsing time measures navigation, not scientific computation."
    )


def main() -> None:
    st.set_page_config(page_title="TransitStress · signal preservation", layout="centered")
    try:
        mode = app_mode()
    except TransitStressError:
        st.error("App mode is invalid. Set TRANSITSTRESS_MODE to demo or local and restart.")
        st.stop()
    st.sidebar.title("TransitStress")
    destination = st.sidebar.radio(
        "Destination", ["Start", "Workbench", "Benchmark", "Methods"], key="destination"
    )
    st.caption(f"TRANSITSTRESS / {destination.upper()} · {mode.upper()} · SAVED ANALYSIS")
    try:
        bundle = public_bundle()
    except TransitStressError as error:
        st.error(str(error))
        st.stop()
    st.sidebar.caption("Data: complete packaged bundle validated · offline · precomputed")
    st.sidebar.caption(
        "Presentation revision: visual feedback and independent review pending. "
        "Dependency findings unresolved; release/privacy holds remain. G4 pending."
    )

    if destination == "Start":
        start(bundle, mode)
    elif destination == "Benchmark":
        benchmark(bundle)
    elif destination == "Methods":
        methods(bundle)
    elif mode == "local":
        content = st.sidebar.radio(
            "Workbench content", ["Local experiment", "Curated examples"], key="workbench_content"
        )
        if content == "Local experiment":
            local.main()
        else:
            curated_workbench(bundle, mode)
    else:
        curated_workbench(bundle)
