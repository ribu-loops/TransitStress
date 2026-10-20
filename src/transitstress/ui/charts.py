"""Reusable presentation-only charts of already computed arrays."""

import numpy as np
import plotly.graph_objects as go
from numpy.typing import NDArray
from plotly.subplots import make_subplots

PIPELINES = (
    ("rm_0p5d", "A · 0.5-day detrending window", "#2457C5", "solid"),
    ("rm_1p5d", "B · 1.5-day detrending window", "#A94D00", "dash"),
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


def processed_chart(
    arrays: dict[str, NDArray], bounds: list[float], layout: str = "Stacked A/B"
) -> go.Figure:
    """Keep every sample; zoom changes only axes, with identical A/B limits."""
    if layout not in {"Stacked A/B", "Overlay"}:
        raise ValueError("processed layout: unsupported; choose Stacked A/B or Overlay")
    stacked = layout == "Stacked A/B"
    fig = (
        make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            shared_yaxes=True,
            vertical_spacing=0.18,
            subplot_titles=[p[1] for p in PIPELINES],
        )
        if stacked
        else go.Figure()
    )
    fig.update_layout(base_chart("Relative time (days)", "Normalized flux (dimensionless)").layout)
    low = min(float(arrays[f"{p[0]}_flux"].min()) for p in PIPELINES)
    high = max(float(arrays[f"{p[0]}_flux"].max()) for p in PIPELINES)
    padding = max((high - low) * 0.05, 1e-6)
    for i, (pipe, label, color, _) in enumerate(PIPELINES, 1):
        trace = go.Scatter(
            x=arrays["time_days"],
            y=arrays[f"{pipe}_flux"],
            mode="markers",
            name=label,
            marker=dict(
                size=3, opacity=0.45, color=color, symbol="circle" if i == 1 else "diamond"
            ),
            hovertemplate=(
                "Time: %{x:.5f} days<br>Normalized flux: %{y:.7f}<extra>%{fullData.name}</extra>"
            ),
        )
        if stacked:
            fig.add_trace(trace, row=i, col=1)
        else:
            fig.add_trace(trace)
    fig.update_xaxes(range=bounds, title_text="Relative time (days)", showticklabels=True)
    fig.update_yaxes(
        range=[low - padding, high + padding],
        title_text="Normalized flux (dimensionless)",
        fixedrange=True,
    )
    if stacked:
        fig.update_yaxes(matches="y", row=2, col=1)
    fig.update_layout(
        height=660 if stacked else 490,
        showlegend=not stacked,
        margin=dict(l=75, r=16, t=40 if stacked else 16, b=64),
    )
    fig.update_annotations(font_size=14)
    return fig


def baseline_context(
    arrays: dict[str, NDArray], baseline: list[float], selected: tuple[float, float]
) -> go.Figure:
    """Always show the full retained injected input as unsmoothed context."""
    fig = base_chart("Relative time (days)", "Normalized flux")
    fig.add_scatter(
        x=arrays["time_days"],
        y=arrays["input_flux"],
        mode="markers",
        name="Injected input · full baseline",
        marker=dict(size=2, opacity=0.45, color="#687386"),
    )
    fig.add_vrect(x0=selected[0], x1=selected[1], fillcolor="#2457C5", opacity=0.08, line_width=0)
    fig.update_xaxes(range=baseline)
    fig.update_layout(height=220, showlegend=False, margin=dict(l=75, r=16, t=8, b=48))
    return fig


def timeline(
    arrays: dict[str, NDArray],
    view: str,
    bounds: list[float],
    processed_layout: str = "Stacked A/B",
) -> go.Figure:
    """One common time/flux scale; lines break at gaps and points stay points."""
    if view == "Processed flux":
        return processed_chart(arrays, bounds, processed_layout)
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
            name=f"{label}<br>Fitted period: {period:.6f} d",
            marker=dict(size=3, color=color, symbol="circle" if pipe == "rm_0p5d" else "x"),
        )
    fig.update_layout(height=460)
    return fig
