"""Render the already computed T04 evidence; this script never runs a search."""

from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from t04_arrays import load_arrays

from transitstress import acquisition as acq


def plot_sanity() -> None:
    root = Path("reports/t04_sanity")
    report = acq.read_json(root, "sanity.json")
    arrays = load_arrays(root, "sanity_arrays.npz", report["numerical_file_sha256"])
    figure = Figure(figsize=(16, 9), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 3)
    for row, (name, color) in enumerate((("rm_0p5d", "#2457C5"), ("rm_1p5d", "#A94D00"))):
        result = report["searches"][name]
        t, f = arrays["time_days"], arrays["flux"]
        trend, z = arrays[f"{name}_trend"], arrays[f"{name}_flux"]
        segments = arrays[f"{name}_segment_ids"]
        timeline, periodogram, folded = axes[row]
        timeline.plot(
            t, (f - 1) * 1e6, ".", color="#687386", ms=1.5, alpha=0.6, label="Prepared PDCSAP"
        )
        for segment in np.unique(segments):
            keep = segments == segment
            timeline.plot(
                t[keep],
                (trend[keep] - 1) * 1e6,
                color=color,
                lw=1,
                label="Median trend" if segment == 0 else None,
            )
        timeline.set(
            title=f"{'A / 0.5 d' if row == 0 else 'B / 1.5 d'}: prepared flux + trend",
            xlabel="Relative time (days)",
            ylabel="Flux deviation (ppm)",
        )
        timeline.legend(fontsize=8)
        periodogram.plot(arrays["period_days"], arrays[f"{name}_power"], color=color, lw=0.6)
        periodogram.axvline(
            result["period_days"],
            color="black",
            ls="--",
            lw=0.7,
            label=f"Fitted {result['period_days']:.6f} d",
        )
        periodogram.set(
            xlabel="Searched period (days)",
            ylabel="BLS likelihood power",
            title="Full independent 0.75–10 d search",
            xlim=(0.75, 10),
        )
        periodogram.legend(fontsize=8)
        period = result["period_days"]
        phase = (t - result["epoch_days"] + period / 2) % period - period / 2
        folded.plot(phase, (z - 1) * 1e6, ".", color="#687386", ms=2, alpha=0.4)
        edges = np.linspace(-0.4, 0.4, 41)
        centers, medians = [], []
        for left, right in zip(edges[:-1], edges[1:], strict=True):
            values = z[(phase >= left) & (phase < right)]
            if len(values):
                centers.append((left + right) / 2)
                medians.append((np.median(values) - 1) * 1e6)
        folded.plot(
            centers, medians, "o-", color=color, ms=3, lw=1, label="0.02 d display-bin median"
        )
        folded.set(
            xlim=(-0.4, 0.4),
            ylim=(-650, 500),
            xlabel="Time from fitted center (days)",
            ylabel="Detrended flux deviation (ppm)",
            title=f"Fitted fold (zoom); depth {result['depth'] * 1e6:.1f} ppm",
        )
        folded.legend(fontsize=8)
        for axis in axes[row]:
            axis.grid(alpha=0.18)
    # Matched scales within each A/B comparison; all scientific points remain saved.
    low = min(axis.get_ylim()[0] for axis in axes[:, 0])
    high = max(axis.get_ylim()[1] for axis in axes[:, 0])
    for axis in axes[:, 0]:
        axis.set_ylim(low, high)
    power_high = max(axis.get_ylim()[1] for axis in axes[:, 1])
    for axis in axes[:, 1]:
        axis.set_ylim(0, power_high)
    figure.suptitle(
        "Pi Mensae c / TIC 261136679 — separate real known-signal sanity check\n"
        "SPOC sector 1 PDCSAP, normalized and 10-minute binned; not a discovery or benchmark",
        fontsize=13,
    )
    figure.savefig(acq.safe_file(root, "sanity.png"), dpi=110)


def plot_synthetic() -> None:
    report = acq.read_json(Path("reports"), "t04_offline_verification.json")
    figure = Figure(figsize=(12, 6), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2)
    for index, (item, axis) in enumerate(zip(report["synthetic_fixtures"], axes.flat, strict=True)):
        arrays = load_arrays(
            Path(".cache/t04_fixtures"), item["numerical_file"], item["numerical_file_sha256"]
        )
        for name, color in (("rm_0p5d", "#2457C5"), ("rm_1p5d", "#A94D00")):
            axis.plot(
                arrays["period_days"],
                arrays[f"{name}_power"],
                color=color,
                lw=0.7,
                alpha=0.8,
                label=f"{name}: fitted {item['searches'][name]['period_days']:.5f} d",
            )
        expected = item["truth_period_days_post_search_only"]
        title = (
            f"Positive fixture, truth {expected} d"
            if expected
            else (
                "Constant negative: zero depth / power"
                if index == 2
                else "Seeded Gaussian negative: uncalibrated peaks"
            )
        )
        if expected:
            axis.axvline(expected, color="#7439A8", ls=":", label="Truth, post-search only")
        axis.set(
            title=title, xlabel="Period (days)", ylabel="BLS likelihood power", xlim=(0.75, 10)
        )
        axis.legend(fontsize=7)
        axis.grid(alpha=0.18)
    figure.suptitle("Synthetic fixtures only — not TESS data or benchmark results", fontsize=13)
    figure.savefig(acq.safe_file(Path("reports/t04_sanity"), "synthetic.png"), dpi=110)


if __name__ == "__main__":
    plot_sanity()
    plot_synthetic()
