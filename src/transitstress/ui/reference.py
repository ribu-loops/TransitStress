"""Approved T08 packaged development presentation; read-only demo entry point."""

from pathlib import Path

import streamlit as st

from transitstress.artifacts import json_read, numeric_read
from transitstress.config import app_mode
from transitstress.errors import TransitStressError
from transitstress.ui.charts import PIPELINES, baseline_context, folded, periodogram, timeline
from transitstress.ui.presentation import show_chart

BUNDLE = Path(__file__).resolve().parents[3] / "demo" / "t08"


def presentation_heading(split: str) -> str:
    """Use the artifact split, never a generic development title for evaluation."""
    if split not in {"development", "evaluation"}:
        raise ValueError("split: unsupported; restore verified artifact metadata")
    return f"Precomputed {split} result"


def load_example() -> tuple[dict, dict]:
    """Read only the fixed packaged path; bounded checksums/schema precede arrays."""
    meta = json_read(BUNDLE, "case.json")
    if meta["split"] != "development" or any(
        r["split"] != "development" or r["case_id"] != meta["case"]["case_id"] for r in meta["rows"]
    ):
        raise ValueError("example identity: inconsistent; restore the packaged development case")
    arrays = numeric_read(BUNDLE, "case.npz", meta["numerical"])
    return meta, arrays


def result_cards(rows: list[dict]) -> None:
    """Native wrapping cards, with labels in addition to pipeline colors."""
    for column, row, (_, label, _, _) in zip(st.columns(2), rows, PIPELINES, strict=True):
        with column, st.container(border=True):
            st.subheader(label)
            st.write("Exact top-peak recovery" if row["exact_recovery"] else "Other top peak")
            st.metric("Fitted period (days)", f"{row['period_days']:.6f}")
            st.write(f"Paired retained depth: {row['retained_depth']['value'] * 1e6:.1f} ppm")
            st.write(f"Retention ratio: {row['retention_ratio']['value']:.3f}")
            match = "yes" if row["background_already_matches"] else "no"
            st.write(f"Background already matches: {match}")


def reset_time_range(bounds: tuple[float, float]) -> None:
    """Reset display axes only; no numerical settings or saved results change."""
    st.session_state["timeline_range"] = bounds


def main() -> None:
    st.set_page_config(page_title="TransitStress · visual reference", layout="centered")
    try:
        mode = app_mode()
    except TransitStressError:
        st.error("App mode is invalid. Set TRANSITSTRESS_MODE to demo or local and restart.")
        st.stop()
    st.caption(f"TRANSITSTRESS / WORKBENCH · {mode.upper()} · T08 visual reference")
    st.title("Does the dip survive?")
    st.write(
        "Compare two detrending configurations on a signal deliberately added to a light curve."
    )
    st.info(
        "Precomputed development example. Demo mode denies uploads and new computation. "
        "For the local workbench, set TRANSITSTRESS_MODE=local and launch on 127.0.0.1."
    )
    try:
        meta, arrays = load_example()
    except (TransitStressError, ValueError, KeyError):
        st.error(
            "Packaged example missing or corrupt. Restore demo/t08 from the verified checkout."
        )
        st.stop()
    case, rows = meta["case"], meta["rows"]
    truth = case["injection"]
    st.subheader(presentation_heading(meta["split"]))
    st.write("Synthetic injection · 1.73 days · 250 ppm · 3 hours · central block removed")
    st.caption(
        "Prepared background: TESS TIC 8195886, sector 1 · PDCSAP, normalized, "
        "10-minute binned. These are not raw pixels or a newly discovered planet."
    )
    row = rows[0]
    st.write(
        f"{row['n_observed']:,} retained / {row['n_original']:,} prepared bins · "
        f"{row['original_bounds_days'][1]:.2f} days baseline · "
        f"{row['observed_transits']} / {len(row['full_centers_days'])} "
        "full events sufficiently observed"
    )
    result_cards(rows)
    st.write(
        "B retains more injected depth in this example, but its strongest BLS peak lies elsewhere. "
        "Paired retention and geometric top-peak recovery answer different questions."
    )
    st.write(
        "Coverage eligible: yes. At least 2 full events with ≥3 retained points each, "
        "and ≥6 in-transit points overall. This example has 223 in-transit points."
    )
    with st.expander("What counts as exact recovery?", expanded=True):
        st.write(
            "Both conditions must hold after the blind search: period error ≤1%, and "
            "the nearest fitted center within half the injected duration at every "
            "sufficiently observed true center. Whole-period epoch shifts are equivalent."
        )
        st.caption(
            "Half/double-period peaks are separate outcomes. Truth only scores and "
            "annotates completed searches; it never guides detrending or the search grid."
        )
    st.subheader("Inspect the paired evidence")
    view = st.radio(
        "Timeline view", ["Processed flux", "Prepared & injected", "Fitted trends"], horizontal=True
    )
    bounds = row["original_bounds_days"]
    if "timeline_range" not in st.session_state:
        st.session_state["timeline_range"] = tuple(bounds)
    selected = st.slider(
        "Displayed time range (relative days)",
        min_value=float(bounds[0]),
        max_value=float(bounds[1]),
        value=None,
        step=0.01,
        format="%.2f days",
        key="timeline_range",
        help="Move both handles to inspect an individual transit window. Only display axes change.",
    )
    st.button("Reset to full baseline", on_click=reset_time_range, args=(tuple(bounds),))
    if selected[0] >= selected[1]:
        st.warning(
            "Displayed time range: choose an end later than the start, or reset to full baseline."
        )
        st.stop()
    st.caption(
        "Full-baseline context · injected input · normalized flux is dimensionless. "
        "Yellow shading marks the displayed time range below."
    )
    show_chart(
        baseline_context(arrays, bounds, selected),
        width="stretch",
        config={"displayModeBar": False},
    )
    layout = "Stacked A/B"
    if view == "Processed flux":
        layout = st.radio("Processed-flux layout", ["Stacked A/B", "Overlay"], horizontal=True)
    st.write(f"Displayed time range: {selected[0]:.2f}–{selected[1]:.2f} relative days")
    show_chart(
        timeline(arrays, view, list(selected), layout),
        width="stretch",
        config={"displayModeBar": False},
    )
    st.caption(
        "All 3,104 retained samples per configuration remain in the chart; the time-range "
        "zoom changes only the displayed interval. No smoothing, averaging, outlier clipping, "
        "interpolation or downsampling. A/B panels share time and flux limits, including "
        "all outliers; observation gaps align. Reset restores the full baseline."
    )
    with st.expander("Broad BLS and fitted-period folds"):
        st.write(
            "BLS likelihood power is a diagnostic, not planet probability. "
            "Yellow dotted line: synthetic injected period, annotated after search."
        )
        show_chart(
            periodogram(arrays, truth["period_days"]),
            width="stretch",
            config={"displayModeBar": False},
        )
        st.write(
            "Fold period source: each configuration's fitted top peak. "
            "All data points shown; no binned summary."
        )
        show_chart(folded(arrays, rows), width="stretch", config={"displayModeBar": False})
    with st.expander("Numbers and provenance"):
        st.dataframe(
            [
                dict(
                    configuration=label,
                    period_days=r["period_days"],
                    period_error_percent=100 * r["relative_period_error"]["value"],
                    max_timing_error_hours=24 * r["maximum_timing_error_days"]["value"],
                    retained_depth_ppm=1e6 * r["retained_depth"]["value"],
                )
                for r, (_, label, _, _) in zip(rows, PIPELINES, strict=True)
            ],
            hide_index=True,
            width="stretch",
        )
        st.text(f"Case: {case['case_id']}\nDevelopment run: {meta['run_id']}")
        st.write(
            "Relative days from BJD(TDB) 2458325.3012118433; normalized flux is dimensionless."
        )
        st.write(
            "Retained depth compares injected and uninjected processing on identical "
            "timestamps. It is a paired synthetic-signal diagnostic, not a planet radius."
        )
    st.subheader("Separate frozen evaluation context")
    st.write(
        "T07: A 79/135 (58.5%); B 74/135 (54.8%) exact geometric recoveries. "
        "Unconditional denominator: 135 planned injection cases per configuration. "
        "Eligible denominator: 135 per configuration. Insufficient coverage: 0; "
        "technical failures: 0. No cases silently excluded."
    )
    st.write(
        "Background coincidences: 0/135 eligible injection cases for each configuration. "
        "Exact matches without a matching uninjected-background top peak: A 79, B 74. "
        "Absence of a matching background peak does not establish causality or authenticity."
    )
    st.warning(
        "135 correlated injection cases across five evaluation stars, with paired "
        "processing by two detrending configurations. These descriptive results do not "
        "establish population superiority, survey completeness or planet confirmation."
    )
    st.caption(
        "Convenience sample selected using diagnostic metadata and prior cache/basic-QC "
        "knowledge; one mask realization, bundled period/phase, one injected duration, "
        "matched box model, upstream PDCSAP correction and binning before injection. "
        "Trend uncertainty/correlation omitted; Gaussian controls are unrealistic. "
        "Real backgrounds are not verified negatives; no calibrated false-alarm rate."
    )
    st.caption(
        "Precomputed development reference. To analyse your own compatible CSV light curve, "
        "use the local Workbench. Local exploratory controls and evidence export require local "
        "mode."
    )
