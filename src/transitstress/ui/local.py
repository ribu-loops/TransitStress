"""Native local workbench; widgets edit a draft and only the Run form starts science."""

import csv
import io
from dataclasses import asdict

import streamlit as st

from transitstress.config import app_mode
from transitstress.errors import TransitStressError
from transitstress.ui import reference
from transitstress.ui.charts import PIPELINES, baseline_context, folded, periodogram, timeline
from transitstress.ui.controller import BUILT_INS, Request, SessionJob, prepare

DEFAULTS = dict(
    source="8195886",
    period=3.41,
    depth=750.0,
    duration=3.0,
    epoch=0.43,
    mask="original",
    confirmed=False,
)


def reset() -> None:
    """Drop this session's private references and replace uploader widget identity."""
    if "job" in st.session_state and st.session_state["job"].running:
        return
    for key in list(st.session_state):
        if key.startswith("upload_") and key != "upload_generation":
            del st.session_state[key]
    for key, value in DEFAULTS.items():
        st.session_state[key] = value
    st.session_state["job"] = SessionJob()
    st.session_state["upload_generation"] = st.session_state.get("upload_generation", 0) + 1
    for key in ("timeline_range", "display_case", "failure"):
        st.session_state.pop(key, None)


def sample_csv() -> bytes:
    """An explicit prepared development subset; no acquisition or new computation."""
    _, arrays = reference.load_example()
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["time_days", "flux", "flux_err"])
    writer.writerows(
        zip(arrays["time_days"], arrays["background_flux"], arrays["input_flux_err"], strict=True)
    )
    return stream.getvalue().encode()


def display_result(job: SessionJob, stale: bool) -> None:
    result, request = job.result, job.request
    rows = [r for r in result["rows"] if r["case_kind"] == "injection"]
    row = rows[0]
    st.subheader("Exploratory result Â· retained executed settings")
    source_label = (
        "CSV Â· absolute epoch unknown"
        if request.source == "CSV"
        else "TESS development TIC " + request.source
    )
    st.text(
        f"Input: {source_label}\n"
        f"Synthetic injection: {request.period_days:g} days Â· {request.depth_ppm:g} ppm Â· "
        f"{request.duration_hours:g} hours Â· epoch fraction {request.epoch_fraction:g} Â· "
        f"{request.mask_name}\n"
        f"Run: {result['run_id']}"
    )
    st.caption(f"Core elapsed: {job.elapsed_seconds:.2f} s Â· request identity {job.fingerprint}")
    for notice in result["notices"]:
        st.info(notice)
    st.write(
        f"{row['n_observed']:,} retained / {row['n_original']:,} prepared bins Â· "
        f"{row['original_bounds_days'][1] - row['original_bounds_days'][0]:.2f} days baseline Â· "
        f"{row['observed_transits']} / {len(row['full_centers_days'] or [])} "
        "full events sufficiently observed Â· "
        f"{row['total_in_transit_points']} in-transit points"
    )
    if row["coverage_eligible"] is False:
        st.warning(
            "Insufficient coverage. Result retained; it is not an exact recovery or a "
            "software failure."
        )
    elif row["coverage_eligible"] is True:
        st.success(
            "Coverage eligible. This describes observed synthetic events, not detection confidence."
        )
    for column, r, (_, label, _, _) in zip(st.columns(2), rows, PIPELINES, strict=True):
        with column, st.container(border=True):
            st.subheader(label)
            st.write(
                "Exact top-peak recovery"
                if r["scientific_outcome"] == "exact"
                else r["scientific_outcome"].replace("_", " ").capitalize()
            )
            st.metric(
                "Fitted period (days)",
                "Unavailable" if r["period_days"] is None else f"{r['period_days']:.6f}",
            )
            for key, text, scale, suffix in (
                ("retained_depth", "Paired retained depth", 1e6, " ppm"),
                ("retention_ratio", "Retention ratio", 1, ""),
            ):
                value = r[key]["value"]
                st.write(
                    f"{text}: {value * scale:.3f}{suffix}"
                    if value is not None
                    else f"{text}: unavailable ({r[key]['reason']})"
                )
            match = r["background_already_matches"]
            st.write(
                "Background already matches: "
                + ("unknown" if match is None else "yes" if match else "no")
            )
            if r["status"] != "ok":
                st.error(
                    "Computation failed. Reset or validate input/support and rerun. "
                    "Failure rows remain in evidence."
                )
    with st.expander("What counts as exact recovery?", expanded=True):
        st.write(
            "After blind search: period error â‰¤1% AND nearest fitted-center error "
            "â‰¤half the injected "
            "duration at every sufficiently observed true center. Whole-period epoch "
            "shifts are equivalent. "
            "Half/double-period peaks are separate outcomes. Eligibility requires â‰¥2 "
            "full events with "
            "â‰¥3 retained points each and â‰¥6 total in-transit points. Truth never "
            "guides detrending or search."
        )
        st.caption(
            "A matching uninjected-background top peak qualifies interpretation; a "
            "nonmatch does not establish causality."
        )
    if all(r["status"] == "ok" for r in rows):
        arrays = result["details"][row["case_id"]]
        bounds = row["original_bounds_days"]
        if (
            st.session_state.get("display_case") != result["run_id"]
            or "timeline_range" not in st.session_state
        ):
            st.session_state["timeline_range"] = tuple(bounds)
            st.session_state["display_case"] = result["run_id"]
        view = st.radio(
            "Timeline view",
            ["Processed flux", "Prepared & injected", "Fitted trends"],
            horizontal=True,
        )
        selected = st.slider(
            "Displayed time range (relative days)",
            min_value=float(bounds[0]),
            max_value=float(bounds[1]),
            step=0.01,
            value=None,
            key="timeline_range",
            format="%.2f days",
        )
        st.button(
            "Reset to full baseline", on_click=reference.reset_time_range, args=(tuple(bounds),)
        )
        layout = (
            st.radio("Processed-flux layout", ["Stacked A/B", "Overlay"], horizontal=True)
            if view == "Processed flux"
            else "Stacked A/B"
        )
        if selected[0] < selected[1]:
            st.caption(
                "Full-baseline context Â· injected input Â· normalized flux is dimensionless."
            )
            st.plotly_chart(
                baseline_context(arrays, bounds, selected),
                width="stretch",
                config={"displayModeBar": False},
            )
            st.plotly_chart(
                timeline(arrays, view, list(selected), layout),
                width="stretch",
                config={"displayModeBar": False},
            )
        else:
            st.warning(
                "Displayed time range: choose an end later than the start, or reset to "
                "full baseline."
            )
        st.caption(
            f"All {len(arrays['time_days']):,} retained samples per configuration. "
            "Display-only time zoom; matched fixed full-extrema flux limits. "
            "No smoothing, averaging, clipping, interpolation or downsampling."
        )
        with st.expander("Broad BLS and fitted-period folds"):
            st.write(
                "Independent broad BLS likelihood power, not planet probability. "
                "Synthetic truth annotated after search. Folds use each fitted top peak."
            )
            st.plotly_chart(
                periodogram(arrays, request.period_days),
                width="stretch",
                config={"displayModeBar": False},
            )
            st.plotly_chart(folded(arrays, rows), width="stretch", config={"displayModeBar": False})
    with st.expander("Numbers, units and provenance"):
        st.dataframe(
            [
                {
                    k: r[k]
                    for k in (
                        "pipeline_id",
                        "status",
                        "scientific_outcome",
                        "period_days",
                        "epoch_days",
                        "depth_snr",
                        "coverage_eligible",
                        "exact_recovery",
                    )
                }
                for r in result["rows"]
            ],
            hide_index=True,
            width="stretch",
        )
        st.json(
            dict(
                units=row["units"],
                requested=request.arguments(),
                identity=result["identity"],
                provenance=result["provenance"],
            )
        )
    try:
        evidence = job.export()
    except (TransitStressError, KeyError, TypeError):
        st.error(
            "Evidence assembly failed. Result remains available; reset and rerun or "
            "restore the verified checkout."
        )
    else:
        st.download_button(
            "Download executed case evidence ZIP" + (" (stale result)" if stale else ""),
            evidence,
            file_name=f"case-{result['run_id'][:16]}.zip",
            mime="application/zip",
            on_click="ignore",
        )
        st.caption(
            "Evidence ready: arrays, four result rows, settings, units, provenance and "
            "self-contained plots. Download saves a private copy only when you choose it."
        )
    st.warning(
        "Exploratory single injection with two configurations and reused background "
        "comparisons. Never added to frozen evaluation tables. BLS/SNR and geometric recovery "
        "are diagnostics, not planet confirmation, calibrated probabilities or survey completeness."
    )


def main() -> None:
    try:
        mode = app_mode()
    except TransitStressError:
        reference.main()
        return
    if mode == "demo":
        reference.main()
        return
    st.set_page_config(page_title="TransitStress Â· local workbench", layout="centered")
    st.caption("TRANSITSTRESS / WORKBENCH Â· LOCAL Â· G3/G4 PENDING")
    st.title("Does the dip survive?")
    st.write(
        "Compare two detrending configurations on a signal deliberately added to a light curve."
    )
    st.info(
        "Local exploratory workbench. Uploads and results stay in this session's "
        "memory. Use on your own loopback server; closing/resetting the session drops app "
        "references. Downloads are your explicit private copies."
    )
    if "job" not in st.session_state:
        reset()
    job = st.session_state["job"]
    st.button("Reset workbench", on_click=reset, disabled=job.running)
    source = st.selectbox(
        "Input light curve",
        [*BUILT_INS, "CSV"],
        key="source",
        format_func=lambda v: "Upload CSV" if v == "CSV" else f"TESS development TIC {v}",
    )
    content = None
    if source == "CSV":
        st.write(
            "UTF-8 uncompressed CSV â‰¤5 MiB / 50,000 rows; unique time_days, flux, "
            "flux_err. Positive finite linear flux/errors; no duplicate times. "
            "Baseline 24â€“40 days, â‰¥2,000 occupied prepared bins. Extra columns are ignored "
            "and excluded from evidence. Absolute epoch remains unknown."
        )
        upload = st.file_uploader(
            "Local CSV (days, linear flux and error)",
            type=["csv"],
            key=f"upload_{st.session_state['upload_generation']}",
        )
        content = upload.getvalue() if upload is not None else None
        try:
            st.download_button(
                "Download example CSV Â· prepared development subset",
                sample_csv(),
                file_name="development-example.csv",
                mime="text/csv",
                on_click="ignore",
            )
        except (TransitStressError, ValueError, KeyError):
            st.error("Example CSV unavailable. Restore demo/t08 from the verified checkout.")
    else:
        st.caption(
            "Prepared TESS PDCSAP development background Â· normalized, 10-minute "
            "binned. Offline pinned cache only; no acquisition. "
            "This is separate from the known-planet sanity example."
        )
    confirmed = st.checkbox(
        "I confirm time is in days and flux/error use the same linear units", key="confirmed"
    )
    # Outside the submission form so edits immediately rerender and mark results stale.
    st.number_input("Injected period (days)", 1.0, 8.0, step=0.01, key="period")
    st.number_input("Injected depth (ppm)", 100.0, 5000.0, step=50.0, key="depth")
    st.number_input("Injected duration (hours)", 1.0, 6.0, step=0.5, key="duration")
    st.number_input(
        "Epoch fraction (0 to less than 1)",
        0.0,
        0.999999,
        step=0.01,
        key="epoch",
        help="Center of the first repeated dip relative to the timeline, as a fraction "
        "of the injected period.",
    )
    st.selectbox("Observation mask", ["original", "random_missing", "central_block"], key="mask")
    request = Request(
        source,
        st.session_state.period,
        st.session_state.depth,
        st.session_state.duration,
        st.session_state.epoch,
        st.session_state.mask,
        confirmed,
        content,
    )
    stale = job.result is not None and request.fingerprint() != job.fingerprint
    status = st.empty()
    if stale:
        status.warning(
            "Result stale: inputs changed. Plots and download below retain the "
            "executed settings. Press Run comparison to update."
        )
    elif job.result is not None:
        status.success("Result current: matches the confirmed input and settings above.")
    valid = False
    if not confirmed:
        st.info("Ready input requires unit confirmation above. No search has started.")
    elif source == "CSV" and content is None:
        st.info("No data: choose a CSV, or select a built-in development input.")
    else:
        try:
            curve, notices = prepare(request)
        except TransitStressError as error:
            # Shared parser messages contain field/count/reason, never cell contents or paths.
            st.error(str(error))
        else:
            valid = True
            st.success(
                f"Input valid Â· {len(curve.time_days):,} prepared bins Â· "
                f"{curve.original_end_days - curve.original_start_days:.2f} days Â· "
                f"{curve.curve_id}"
            )
            st.text(
                f"Input units: {asdict(curve.time_metadata)}\n"
                f"Prepared content SHA-256: {curve.content_hash}"
            )
            for notice in notices:
                st.info(notice)
    with st.form("run_comparison"):
        st.caption(
            "Run one synthetic injection with fixed A/B windows and independent "
            "0.75â€“10-day search settings. Widget edits and display zoom do not run science."
        )
        submitted = st.form_submit_button(
            "Run comparison", disabled=not valid or job.running, type="primary"
        )
    if submitted:
        progress = st.progress(0, text="Validated input; starting paired comparison")
        try:
            with st.spinner(
                "Running frozen T06 core: independent backgrounds, then injected A/B",
                show_time=True,
            ):
                changed = job.run(request)
        except TransitStressError as error:
            st.error(str(error))
            progress.empty()
        else:
            progress.progress(
                100,
                text="Comparison complete"
                if changed
                else "Identical completed comparison reused in this session",
            )
            stale = False
            status.success("Result current: matches the confirmed input and settings above.")
    if job.result is not None:
        display_result(job, stale)
