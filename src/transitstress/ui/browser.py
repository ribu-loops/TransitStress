"""Four native destinations consuming validated, precomputed public evidence."""

import streamlit as st

from transitstress.artifacts import results_csv
from transitstress.config import app_mode
from transitstress.errors import TransitStressError
from transitstress.ui import local, reference
from transitstress.ui.bundle import DemoBundle, case_zip, counts
from transitstress.ui.charts import PIPELINES, baseline_context, folded, periodogram, timeline
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
    st.write("See whether preprocessing preserves a signal deliberately added to a light curve.")
    st.write(
        "Observe brightness over time → add a periodic synthetic dip → compare two "
        "rolling-median detrending windows → inspect a blind BLS top peak."
    )
    st.button(
        "Explore a curated case",
        type="primary",
        on_click=navigate,
        args=("Workbench", "Curated examples"),
    )
    st.button("Open local workbench", on_click=navigate, args=("Workbench", "Local experiment"))
    if mode == "demo":
        st.info(
            "Demo browses precomputed artifacts. Open local workbench shows the launch "
            "instructions; uploads and fresh computation are denied here."
        )
    st.write(
        "Frozen evaluation: 135 injection cases across five stars; two configurations "
        "process each case. Local workbench runs are exploratory and never enter this table. "
        "No illustrative fixtures are presented as measured findings."
    )
    recovery_cards(bundle.rows)
    sanity_view(bundle)
    safety_caption()


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


def detail(bundle: DemoBundle, cid: str) -> None:
    rows = [r for r in bundle.rows if r["case_id"] == cid]
    row = rows[0]
    st.text(f"Case ID: {cid}")
    st.caption(f"TIC {tic_id(row)} · {row['case_kind']} · {row['mask_name']}")
    if cid not in bundle.arrays:
        st.info(
            "Summary-only row: detailed evidence unavailable in this demo. "
            "Download the saved pair below. To inspect full arrays locally, restore "
            "the matching original run and validate it; an exploratory rerun cannot "
            "replace frozen evidence."
        )
        st.code(
            "uv run --locked python -m transitstress validate-artifact "
            "--path runs/t07_evaluation_20261020T1857Z",
            language="powershell",
        )
    else:
        selected = next(c for c in bundle.manifest["curated"] if c["case_id"] == cid)
        st.subheader("Precomputed evaluation result")
        st.write("Selection: " + "; ".join(selected["roles"]))
        st.write(
            f"Synthetic injection · {row['true_period_days']:g} days · "
            f"{row['true_depth'] * 1e6:g} ppm · "
            f"{row['true_duration_days'] * 24:g} hours · {row['mask_name']}"
        )
        st.caption(
            "Prepared TESS PDCSAP background, normalized and 10-minute binned; "
            "not raw telescope pixels. All retained samples shown."
        )
        st.write(
            f"{row['n_observed']:,} retained / {row['n_original']:,} prepared bins · "
            f"{row['observed_transits']}/{len(row['full_centers_days'])} full events "
            f"sufficiently observed · {row['total_in_transit_points']} in-transit points."
        )
        reference.result_cards(rows)
        if selected["stem"] == "example3":
            st.write(
                "The central block removes the event near day 11.6793; three full events "
                "remain sufficiently observed. A matches the injected timing. B retains "
                "more incremental depth but its strongest peak is elsewhere. Its background "
                "already peaks at that other period, consistent with background structure "
                "outranking the injection, without identifying its physical origin."
            )
        elif selected["stem"] == "example2":
            st.write(
                "Both top peaks miss the injected timing/period despite eligible coverage. "
                "This is the smallest eligible saved retention ratio, selected after analysis."
            )
        else:
            st.write(
                "Both configurations match the injected period and timing in this selected "
                "example. One success does not describe every case."
            )
        arrays = bundle.arrays[cid]
        bounds = row["original_bounds_days"]
        if (
            st.session_state.get("public_display_case") != cid
            or "public_time_range" not in st.session_state
        ):
            st.session_state["public_time_range"] = tuple(bounds)
            st.session_state["public_display_case"] = cid
        view = st.radio(
            "Timeline view",
            ["Processed flux", "Prepared & injected", "Fitted trends"],
            horizontal=True,
        )
        zoom = st.slider(
            "Displayed time range (relative days)",
            float(bounds[0]),
            float(bounds[1]),
            value=None,
            step=0.01,
            format="%.2f days",
            key="public_time_range",
        )
        st.button("Reset to full baseline", on_click=_reset_public, args=(tuple(bounds),))
        layout = (
            st.radio("Processed-flux layout", ["Stacked A/B", "Overlay"], horizontal=True)
            if view == "Processed flux"
            else "Stacked A/B"
        )
        if zoom[0] < zoom[1]:
            st.caption("Full-baseline context · injected input · normalized flux is dimensionless.")
            st.plotly_chart(
                baseline_context(arrays, bounds, zoom),
                width="stretch",
                config={"displayModeBar": False},
            )
            st.plotly_chart(
                timeline(arrays, view, list(zoom), layout),
                width="stretch",
                config={"displayModeBar": False},
            )
        else:
            st.warning("Displayed time range: choose an end later than the start, or reset.")
        st.caption(
            f"All {len(arrays['time_days']):,} samples per configuration. "
            "Display-only zoom; matched time and fixed full-extrema flux limits; "
            "aligned gaps. No smoothing, averaging, clipping or downsampling."
        )
        with st.expander("Broad BLS and fitted-period folds"):
            st.write(
                "BLS likelihood power, not planet probability. Purple line: synthetic "
                "truth annotated after search. Folds use each fitted top peak."
            )
            st.plotly_chart(
                periodogram(arrays, row["true_period_days"]),
                width="stretch",
                config={"displayModeBar": False},
            )
            st.plotly_chart(folded(arrays, rows), width="stretch", config={"displayModeBar": False})
    with st.expander("Exact saved values, null reasons and units"):
        st.json(rows)
    st.download_button(
        "Download selected pair evidence ZIP",
        case_zip(bundle, cid),
        file_name=f"frozen-case-{cid[:16]}.zip",
        mime="application/zip",
        on_click="ignore",
    )


def _reset_public(bounds: tuple[float, float]) -> None:
    st.session_state["public_time_range"] = bounds


def curated_workbench(bundle: DemoBundle, mode: str = "demo") -> None:
    st.title("Workbench · precomputed examples")
    st.info(
        (
            "Demo mode denies uploads and new computation. "
            if mode == "demo"
            else "Choose Local experiment in the sidebar for an exploratory run. "
        )
        + "Selecting a saved case only changes the display; there is no Run simulation."
    )
    with st.expander("Launch the local exploratory workbench"):
        st.write(
            "Use your own checkout with its locked environment and pinned offline data. "
            "Local launches bind to loopback; this demo never browses a local cache."
        )
        st.code(
            '$env:TRANSITSTRESS_MODE = "local"\n'
            "uv run --locked streamlit run app.py --server.address 127.0.0.1",
            language="powershell",
        )
    examples = bundle.manifest["curated"]
    cid = st.selectbox(
        "Curated detailed case",
        [c["case_id"] for c in examples],
        format_func=lambda value: next(
            c["roles"][0].split(":")[0] + " · " + value[:12]
            for c in examples
            if c["case_id"] == value
        ),
    )
    detail(bundle, cid)
    safety_caption()


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
    stars = sorted({tic_id(r) for r in bundle.rows})
    star = st.selectbox("Evaluation star (TIC)", ["All", *stars])
    mask = st.selectbox("Missingness", ["All", "original", "random_missing", "central_block"])
    depth = st.selectbox("Injected depth (ppm)", ["All", 250, 750, 2250])
    family = st.selectbox("Injected family period (days)", ["All", 1.73, 3.41, 6.83])
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
    kind = st.selectbox("Table case kind", ["All", "injection", "uninjected_real", "gaussian_null"])
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
    )
    status = st.selectbox(
        "Table processing status",
        ["All", "ok", "invalid_input", "detrend_error", "search_error", "artifact_error"],
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
    if shown:
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
    with st.expander(
        "Precise injection, recovery, coverage and retention definitions", expanded=True
    ):
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
    sanity_view(bundle)
    identity = bundle.identity["identity"]
    st.subheader("Artifact and software provenance")
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
        "is preserved. G3 product/results and G4 release review remain pending."
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
        "The demo is self-contained. The last command additionally requires the original "
        "run restored locally. Clone/source-data reproduction, native-link proof, "
        "current online audit and all-history secret scan remain pending. No release "
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
    st.caption(f"TRANSITSTRESS / {destination.upper()} · {mode.upper()} · G3/G4 PENDING")
    try:
        bundle = public_bundle()
    except TransitStressError as error:
        st.error(str(error))
        st.stop()
    st.sidebar.caption("Data: complete packaged bundle validated · offline · precomputed")
    st.caption("Public evidence: packaged artifacts validated · precomputed · offline")
    if mode == "local" and destination != "Workbench":
        # Native widget keys otherwise expire when a destination omits them.
        # Keep only this session's bounded draft; never execute or cache it.
        for key in local.DEFAULTS:
            if key in st.session_state:
                st.session_state[key] = st.session_state[key]
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
