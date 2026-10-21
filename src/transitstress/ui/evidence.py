"""In-memory presentation assembly; reuse frozen numeric and CSV validation."""

import hashlib
import html
import io
import json
import zipfile

import numpy as np

from transitstress.artifacts import _numeric, results_csv, validate_rows
from transitstress.errors import DataValidationError
from transitstress.provenance import canonical_hash, canonical_json, numeric_hash
from transitstress.ui.charts import folded, periodogram, timeline
from transitstress.ui.controller import Request


def evidence_zip(result: dict, request: Request, fingerprint: str) -> bytes:
    """Fixed member names, controlled chart labels, no upload names or source bytes."""
    if result["run_id"] != canonical_hash(result["identity"]) or not result["exploratory"]:
        raise DataValidationError("evidence identity: inconsistent; run again and preserve failure")
    rows = result["rows"]
    ids = list(dict.fromkeys(r["case_id"] for r in rows))
    if len(ids) != 2 or len(rows) != 4:
        raise DataValidationError("evidence rows: incomplete; preserve and rerun the single case")
    validate_rows(rows, [{"case_id": cid} for cid in ids])
    injected = [r for r in rows if r["case_kind"] == "injection"]
    arrays = result["details"][injected[0]["case_id"]]
    members = {"results.csv": results_csv(rows)}
    numerical = {}
    for kind, name in (
        ("injection", "injected_arrays.npz"),
        ("uninjected_real", "background_arrays.npz"),
    ):
        cid = next(r["case_id"] for r in rows if r["case_kind"] == kind)
        bounded = _numeric(result["details"][cid])
        stream = io.BytesIO()
        np.savez_compressed(stream, **bounded)
        members[name] = stream.getvalue()
        numerical[name] = dict(
            transport_sha256=hashlib.sha256(members[name]).hexdigest(),
            numeric_hash=numeric_hash(bounded),
            arrays={k: dict(length=len(v), dtype=v.dtype.str) for k, v in bounded.items()},
        )
    settings = dict(
        exploratory=True,
        draft_fingerprint=fingerprint,
        input_source=request.source,
        source_csv_sha256=hashlib.sha256(request.csv_content).hexdigest()
        if request.csv_content
        else None,
        requested=request.arguments(),
        units=injected[0]["units"],
        injection=result["identity"]["settings"]["spec"],
        independent_search=result["identity"]["settings"]["protocol"]["search"],
        notices=result["notices"],
        numerical=numerical,
    )
    for name, value in (
        ("results.json", dict(run_id=result["run_id"], rows=rows)),
        ("settings.json", settings),
        (
            "provenance.json",
            dict(
                run_id=result["run_id"],
                identity=result["identity"],
                provenance=result["provenance"],
            ),
        ),
    ):
        members[name] = canonical_json(value) + b"\n"
    page = (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>TransitStress exploratory evidence</title><h1>Exploratory synthetic injection</h1>"
        "<p>One injection, two detrending configurations, reused independent "
        "background comparisons. "
        "Not part of the frozen evaluation. Geometric recovery and BLS peaks are diagnostics, "
        "not planet confirmation or calibrated probabilities. All retained samples shown.</p>"
        "<p>Exact recovery requires period error ≤1% and center agreement within "
        "half the injected "
        "duration at every sufficiently observed full event. Eligibility: at least two full events "
        "with three retained points each and six total in-transit points. Failures and "
        "null reasons "
        "remain in results.json. Background coincidence does not establish causality.</p><pre>"
        + html.escape(json.dumps(settings, indent=2, allow_nan=False))
        + "</pre><h2>Executed result rows (including backgrounds and failures)</h2><pre>"
        + html.escape(json.dumps(rows, indent=2, allow_nan=False))
        + "</pre>"
    )
    if all(r["status"] == "ok" for r in injected):
        figures = [
            timeline(arrays, "Prepared & injected", injected[0]["original_bounds_days"]),
            timeline(arrays, "Fitted trends", injected[0]["original_bounds_days"]),
            timeline(arrays, "Processed flux", injected[0]["original_bounds_days"]),
            periodogram(arrays, injected[0]["true_period_days"]),
            folded(arrays, injected),
        ]
        for i, figure in enumerate(figures):
            page += figure.to_html(
                full_html=False,
                include_plotlyjs=i == 0,
                div_id=f"evidence-plot-{i}",
                config=dict(displaylogo=False, responsive=True),
            )
    else:
        page += "<p>Technical failure: plots unavailable. Retained rows describe the failure.</p>"
    members["report.html"] = (page + "</html>").encode("utf-8")
    manifest = {name: hashlib.sha256(content).hexdigest() for name, content in members.items()}
    members["checksums.json"] = canonical_json(manifest)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            if len(content) > 16 * 1024**2:
                raise DataValidationError("evidence member: over 16 MiB; preserve bounded case")
            archive.writestr(name, content)
    if len(stream.getbuffer()) > 16 * 1024**2:
        raise DataValidationError("evidence ZIP: over 16 MiB; preserve bounded case")
    return stream.getvalue()
