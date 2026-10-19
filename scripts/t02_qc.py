"""Render actual pinned prepared backgrounds and occupied-bin spacings, offline."""

import io
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

from transitstress.acquisition import (  # noqa: E402
    atomic_write,
    cached_source,
    read_json,
    validate_manifest,
    verify_pinned,
    write_json,
)
from transitstress.archive import source_sha256  # noqa: E402
from transitstress.config import load_config  # noqa: E402
from transitstress.errors import DataValidationError  # noqa: E402


def main() -> None:
    config = load_config(Path("configs/protocol_v1.toml"))
    manifest = read_json(Path("data"), "manifest.json")
    validate_manifest(manifest, config)
    flux_figure = Figure(figsize=(14, 12), layout="constrained")
    gap_figure = Figure(figsize=(14, 12), layout="constrained")
    flux_axes = flux_figure.subplots(4, 2).ravel()
    gap_axes = gap_figure.subplots(4, 2).ravel()
    evidence = []
    for product, flux_axis, gap_axis in zip(manifest["products"], flux_axes, gap_axes, strict=True):
        content = cached_source(Path(".cache/transitstress"), product)
        if content is None:
            raise DataValidationError("QC cache: missing; fetch the exact pinned sources first")
        curve = verify_pinned(product, content, config, Path(".cache/transitstress"))
        label = f"TIC {product['tic_id']} | {product['role']}"
        flux_axis.scatter(curve.time_days, curve.flux, s=2, color="#136b8a", linewidths=0)
        flux_axis.set(title=label, xlabel="Relative time (days)", ylabel="Normalized PDCSAP flux")
        flux_axis.ticklabel_format(axis="y", useOffset=False)
        gap_axis.scatter(
            curve.time_days[1:],
            np.diff(curve.time_days) * 1440,
            s=3,
            color="#8a4c13",
            linewidths=0,
        )
        gap_axis.set(
            title=label,
            xlabel="Relative time (days)",
            ylabel="Occupied-bin spacing (minutes)",
            yscale="log",
        )
        evidence.append(
            {
                "tic_id": product["tic_id"],
                "prepared_hash": curve.content_hash,
                "plotted_points": len(curve.time_days),
                "display_downsampled": False,
            }
        )
    flux_figure.suptitle(
        "T02: actual prepared backgrounds | PDCSAP, normalized, 10-minute binned\n"
        "Provisional basic QC only; shared support eligibility remains for T03",
        fontsize=14,
    )
    gap_figure.suptitle(
        "T02: spacings between occupied prepared bins | empty bins remain absent\n"
        "Markers preserve gaps; no interpolation, injection or benchmark search",
        fontsize=14,
    )
    files = []
    for figure, name in (
        (flux_figure, "t02_prepared_backgrounds.png"),
        (gap_figure, "t02_prepared_spacings.png"),
    ):
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=130)
        content = buffer.getvalue()
        atomic_write(Path("reports"), name, content)
        files.append({"filename": name, "sha256": source_sha256(content)})
    write_json(
        Path("reports"),
        "t02_qc_evidence.json",
        {"status": "provisional", "products": evidence, "figures": files},
    )
    print("QC: rendered 8 pinned prepared curves and spacings offline; no searches")


if __name__ == "__main__":
    main()
