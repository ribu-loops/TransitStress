"""Reusable presentation-only charts of already computed arrays."""

import numpy as np
import plotly.graph_objects as go
from numpy.typing import NDArray

PIPELINES = (
    ("rm_0p5d", "A / 0.5 d", "#2457C5", "solid"),
    ("rm_1p5d", "B / 1.5 d", "#A94D00", "dash"),
)


def gap_line(time: NDArray, values: NDArray) -> tuple[list, list]:
    """Insert display-only breaks at the frozen 0.5-day segment boundary."""
    x, y = [], []
    for i, (t, value) in enumerate(zip(time, values, strict=True)):
        if i and t - time[i - 1] > 0.5:
            x.append(None)
            y.append(None)
        x.append(float(t))
        y.append(float(value))
    return x, y


def base_chart(x_label: str, y_label: str) -> go.Figure:
    """Readable shared chart defaults, including wrapping-safe vertical legends."""
    fig = go.Figure()
    fig.update_layout(
        template="plotly_white",
        height=390,
        font=dict(family="Arial, sans-serif", size=16, color="#172033"),
        margin=dict(l=65, r=16, t=16, b=64),
        legend=dict(
            orientation="v",
            x=0,
            xanchor="left",
            yanchor="top",
            y=-0.25,
            font=dict(size=14),
        ),
        xaxis_title=x_label,
        yaxis_title=y_label,
    )
    return fig


def timeline(arrays: dict[str, NDArray], view: str, bounds: list[float]) -> go.Figure:
    """One common time/flux scale; lines break at gaps and points stay points."""
    fig = base_chart("Relative time (days)", "Normalized flux (dimensionless)")
    t = arrays["time_days"]
    if view == "Prepared & injected":
        for key, label, color in (
            ("background_flux", "Prepared background", "#687386"),
            ("input_flux", "With synthetic injection", "#7439A8"),
        ):
            fig.add_scatter(
                x=t, y=arrays[key], mode="markers", name=label, marker=dict(size=3, color=color)
            )
    else:
        suffix = "trend" if view == "Fitted trends" else "flux"
        if suffix == "trend":
            fig.add_scatter(
                x=t,
                y=arrays["input_flux"],
                mode="markers",
                name="Injected input",
                marker=dict(size=3, color="#687386"),
            )
        for pipe, label, color, dash in PIPELINES:
            x, y = gap_line(t, arrays[f"{pipe}_{suffix}"])
            fig.add_scatter(
                x=x,
                y=y,
                mode="lines",
                name=label,
                line=dict(color=color, dash=dash, width=1.5),
                connectgaps=False,
            )
    fig.update_xaxes(range=bounds)
    fig.update_layout(height=490)
    return fig


def periodogram(arrays: dict[str, NDArray], true_period: float) -> go.Figure:
    """Broad precomputed BLS objective with post-search synthetic truth annotation."""
    fig = base_chart("Period (days)", "BLS likelihood power")
    for pipe, label, color, dash in PIPELINES:
        power = arrays[f"{pipe}_bls_power"].copy()
        power[arrays[f"{pipe}_bls_power_valid"] == 0] = np.nan
        fig.add_scatter(
            x=arrays[f"{pipe}_bls_period_days"],
            y=power,
            name=label,
            mode="lines",
            line=dict(color=color, dash=dash),
        )
    fig.add_vline(x=true_period, line_color="#7439A8", line_dash="dot")
    fig.update_xaxes(range=[0.75, 10])
    fig.update_layout(height=460)
    return fig


def folded(arrays: dict[str, NDArray], rows: list[dict]) -> go.Figure:
    """Fitted-period folds; shared y scale, all points, no interpolation/binning."""
    fig = base_chart("Time from fitted center (days)", "Normalized flux (dimensionless)")
    for row, (pipe, label, color, _) in zip(rows, PIPELINES, strict=True):
        period = row["period_days"]
        phase = (arrays["time_days"] - row["epoch_days"] + period / 2) % period - period / 2
        fig.add_scatter(
            x=phase,
            y=arrays[f"{pipe}_flux"],
            mode="markers",
            name=f"{label}: {period:.6f} d",
            marker=dict(size=3, color=color, symbol="circle" if pipe == "rm_0p5d" else "x"),
        )
    fig.update_layout(height=460)
    return fig
