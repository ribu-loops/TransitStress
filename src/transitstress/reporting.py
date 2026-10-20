"""Descriptive analysis and escaped self-contained HTML; no scientific processing."""

import csv
import html
import io
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from transitstress import acquisition as acq
from transitstress.artifacts import (
    json_read,
    json_write,
    results_csv,
    validate_numeric_inputs,
    validate_rows,
    verify_checksum,
)
from transitstress.errors import DataValidationError
from transitstress.experiment import read_case
from transitstress.provenance import canonical_hash


def validate_artifact(root: Path) -> tuple[dict, list[dict]]:
    try:
        return _validate_artifact(root)
    except (KeyError, TypeError, IndexError, OSError) as error:
        raise DataValidationError(
            "artifact schema: missing/invalid fields; restore generated run"
        ) from error


def _validate_artifact(root: Path) -> tuple[dict, list[dict]]:
    run = json_read(root, "run.json")
    if (
        run["schema_version"] != 1
        or run["run_id"] != canonical_hash(run["identity"])
        or run["plan_hash"] != canonical_hash(run["plan"])
        or run["expected_rows"] != len(run["plan"]) * 2
        or not 0 < run["expected_rows"] <= 1000
    ):
        raise DataValidationError("run schema: invalid identities/counts; restore run record")
    if run["status"] != "complete" or run["actual_rows"] != run["expected_rows"]:
        raise DataValidationError("run status: incomplete; resume before complete analysis")
    for name, checksum in (run["input_checksums"] | run["file_checksums"]).items():
        parts = name.split("/")
        if len(parts) > 2 or (len(parts) == 2 and parts[0] not in ("inputs", "masks", "grids")):
            raise DataValidationError("artifact path: invalid; restore generated names")
        folder = root if len(parts) == 1 else root / parts[0]
        if not verify_checksum(folder, parts[-1], checksum):
            raise DataValidationError("artifact checksum: changed; preserve and diagnose")
    if set(run["completed"]) != {c["case_id"] for c in run["plan"]}:
        raise DataValidationError("run cases: missing/extra; restore complete plan")
    validate_numeric_inputs(root)
    rows = []
    for case in run["plan"]:
        pair, _ = read_case(root, case, run["completed"][case["case_id"]], run["run_id"])
        rows.extend(pair)
    validate_rows(rows, run["plan"])
    if json_read(root, "results.json")["rows"] != rows:
        raise DataValidationError("aggregate rows: differ from cases; regenerate verified outputs")
    if acq.safe_file(root, "results.csv").read_bytes() != results_csv(rows):
        raise DataValidationError("CSV projection: differs from scientific rows; restore export")
    return run, rows


def analyze(root: Path) -> dict:
    run, rows = validate_artifact(root)
    groups = defaultdict(list)
    for row in rows:
        if row["case_kind"] == "injection":
            for dimension, value in (
                ("all", "all"),
                ("star", row["curve_id"]),
                ("mask", row["mask_name"]),
                ("depth", row["true_depth"]),
                ("depth_mask", [row["true_depth"], row["mask_name"]]),
            ):
                groups[row["pipeline_id"], dimension, str(value)].append(row)
    summaries = []
    for (pipeline, dimension, value), group in sorted(groups.items()):
        exact = [r for r in group if r["exact_recovery"] is True and r["status"] == "ok"]
        eligible = sum(r["coverage_eligible"] is True for r in group)
        summaries.append(
            dict(
                pipeline_id=pipeline,
                dimension=dimension,
                group=value,
                planned=len(group),
                eligible=eligible,
                exact=len(exact),
                exact_fraction_all=len(exact) / len(group),
                exact_fraction_eligible=len(exact) / eligible if eligible else None,
                eligible_fraction_reason=None if eligible else "no_eligible_cases",
                insufficient_coverage=sum(r["coverage_eligible"] is False for r in group),
                technical_failures=sum(r["status"] != "ok" for r in group),
                exact_with_background=sum(r["background_already_matches"] is True for r in exact),
                exact_without_background=sum(
                    r["background_already_matches"] is False for r in exact
                ),
                exact_background_unknown=sum(
                    r["background_already_matches"] is None for r in exact
                ),
            )
        )
    pairs = defaultdict(dict)
    for row in rows:
        if row["case_kind"] == "injection":
            pairs[row["case_id"]][row["pipeline_id"]] = row
    differences = []
    for cid, pair in pairs.items():
        a, b = pair["rm_0p5d"], pair["rm_1p5d"]
        x, y = a["retention_ratio"]["value"], b["retention_ratio"]["value"]
        differences.append(
            dict(
                case_id=cid,
                curve_id=a["curve_id"],
                exact_a=a["exact_recovery"],
                exact_b=b["exact_recovery"],
                retention_b_minus_a=None if x is None or y is None else y - x,
                retention_difference_reason="paired_retention_unavailable"
                if x is None or y is None
                else None,
            )
        )
    report = dict(
        schema_version=1,
        run_id=run["run_id"],
        split=run["identity"]["split"],
        row_counts=dict(Counter(r["case_kind"] for r in rows)),
        statuses=dict(Counter(r["status"] for r in rows)),
        summaries=summaries,
        paired_differences=differences,
        limitations=[
            "Repeated conditions on the same parents are correlated.",
            "Geometric matches and BLS/SNR diagnostics are not planet confirmation "
            "or calibrated probabilities.",
            "One mask realization, bundled period/phase, one box duration, "
            "upstream corrected flux and binning before injection.",
            "Trend uncertainty and induced error correlations are omitted; "
            "Gaussian nulls omit correlated noise.",
            "Development evidence is not held-out evaluation or survey completeness.",
        ],
    )
    json_write(root, "summary.json", report)
    # Summary CSV has controlled pipeline/dimension plus generated group hashes,
    # avoiding arbitrary curve labels in spreadsheet cells.
    stream = io.StringIO(newline="")
    fields = ("pipeline_id", "group_id", "planned", "eligible", "exact", "technical_failures")
    writer = csv.DictWriter(stream, fields, lineterminator="\n")
    writer.writeheader()
    for group in summaries:
        writer.writerow(
            {
                key: canonical_hash([group["dimension"], group["group"]])
                if key == "group_id"
                else group[key]
                for key in fields
            }
        )
    acq.atomic_write(root, "summary.csv", stream.getvalue().encode())
    content = (
        "<!doctype html><meta charset=utf-8><title>TransitStress evidence</title>"
        "<h1>Paired exploratory/development evidence</h1>"
        "<p>All planned rows and technical failures retained. "
        "BLS peaks are diagnostics. No planet confirmation or completeness claim.</p><pre>"
        + html.escape(json.dumps(report, indent=2, allow_nan=False))
        + "</pre>"
    )
    acq.atomic_write(root, "report.html", content.encode())
    selected = next((case for case in run["plan"] if case["kind"] == "injection"), None)
    if selected is not None:
        pair, arrays = read_case(
            root, selected, run["completed"][selected["case_id"]], run["run_id"]
        )
        if "time_days" in arrays:
            acq.atomic_write(root, "report.html", (content + _plots(pair, arrays)).encode())
    return report


def _plots(rows: list[dict], arrays: dict) -> str:
    """Fixed titles/labels only; untrusted names never enter embedded script data."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    figure = make_subplots(
        rows=4,
        cols=2,
        subplot_titles=(
            "A: input and independent trend",
            "B: input and independent trend",
            "A: detrended flux",
            "B: detrended flux",
            "A: broad BLS",
            "B: broad BLS",
            "A: fitted-period fold",
            "B: fitted-period fold",
        ),
    )
    time = arrays["time_days"]

    # Break plotted lines at large gaps. Stored arrays remain untouched.
    def gap_series(values: np.ndarray) -> tuple[list, list]:
        x, y = [], []
        for i, (t, value) in enumerate(zip(time, values, strict=True)):
            if i and t - time[i - 1] > 0.5:
                x.append(None)
                y.append(None)
            x.append(float(t))
            y.append(float(value))
        return x, y

    for col, row in enumerate(rows, 1):
        pipe = row["pipeline_id"]
        if f"{pipe}_trend" not in arrays:
            continue
        for key, label in (
            ("input_flux", "Injected input"),
            (f"{pipe}_trend", "Independent trend"),
        ):
            x, y = gap_series(arrays[key])
            figure.add_trace(go.Scatter(x=x, y=y, mode="lines", name=label), row=1, col=col)
        x, y = gap_series(arrays[f"{pipe}_flux"])
        figure.add_trace(go.Scatter(x=x, y=y, mode="lines", name="Processed"), row=2, col=col)
        key = f"{pipe}_bls_power"
        if key in arrays:
            powers = arrays[key].copy()
            powers[arrays[f"{key}_valid"] == 0] = np.nan
            figure.add_trace(
                go.Scatter(
                    x=arrays[f"{pipe}_bls_period_days"],
                    y=powers,
                    mode="lines",
                    name="BLS diagnostic",
                ),
                row=3,
                col=col,
            )
        if row["period_days"] is not None:
            phase = (time - row["epoch_days"] + row["period_days"] / 2) % row["period_days"] - row[
                "period_days"
            ] / 2
            figure.add_trace(
                go.Scatter(
                    x=phase,
                    y=arrays[f"{pipe}_flux"],
                    mode="markers",
                    marker=dict(size=3),
                    name="Fitted fold",
                ),
                row=4,
                col=col,
            )
        for number, x_label, y_label in (
            (1, "Relative time (days)", "Normalized flux"),
            (2, "Relative time (days)", "Detrended flux"),
            (3, "Period (days)", "BLS likelihood power"),
            (4, "Time from fitted center (days)", "Detrended flux"),
        ):
            figure.update_xaxes(title_text=x_label, row=number, col=col)
            figure.update_yaxes(
                title_text=y_label,
                matches=f"y{2 * number - 1}" if col == 2 else None,
                row=number,
                col=col,
            )
        figure.update_xaxes(range=[0.75, 10], row=3, col=col)
    figure.update_layout(
        height=1300, showlegend=False, title="Selected injected case: diagnostic evidence"
    )
    return figure.to_html(
        full_html=False, include_plotlyjs=True, config=dict(displaylogo=False, responsive=True)
    )


def export_case_zip(root: Path) -> None:
    """Explicit exploratory export; fixed members, never an arbitrary directory ZIP."""
    run, _ = validate_artifact(root)
    if run["identity"]["split"] != "exploratory" or len(run["plan"]) != 2:
        raise DataValidationError("case ZIP: requires one exploratory pair and its background")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in (
            "results.csv",
            "results.json",
            "settings.json",
            "run.json",
            "summary.json",
            "report.html",
        ):
            path = acq.safe_file(root, name)
            if path.stat().st_size > 16 * 1024**2:
                raise DataValidationError("ZIP member: oversized; preserve bounded evidence")
            archive.writestr(name, path.read_bytes())
        for case in run["plan"]:
            name = "injected_arrays.npz" if case["kind"] == "injection" else "background_arrays.npz"
            archive.writestr(
                name, acq.safe_file(root / "details", f"{case['case_id']}.npz").read_bytes()
            )
    if len(stream.getbuffer()) > 16 * 1024**2:
        raise DataValidationError("ZIP: over 16 MiB; preserve bounded export")
    acq.atomic_write(root, "evidence.zip", stream.getvalue())
