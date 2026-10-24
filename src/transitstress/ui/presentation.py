"""Screen-only styling: historical chart builders and exports keep their defaults."""

import plotly.graph_objects as go
import streamlit as st

BACKGROUND = "#111827"
SURFACE = "#1B2638"
TEXT = "#F1F5F9"
YELLOW = "#F4CE68"


def dark_figure(figure: go.Figure) -> go.Figure:
    """Style a copy, preserving samples, scales, gap breaks and historical exports."""
    fig = go.Figure(figure)
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BACKGROUND,
        plot_bgcolor=BACKGROUND,
        font=dict(color=TEXT, size=16),
        hoverlabel=dict(bgcolor=SURFACE, font_color=TEXT, font_size=14),
        legend=dict(bgcolor=BACKGROUND, font_color=TEXT),
    )
    fig.update_xaxes(gridcolor="#344155", zerolinecolor="#344155", automargin=True)
    fig.update_yaxes(gridcolor="#344155", zerolinecolor="#344155", automargin=True)
    for trace in fig.data:
        name = trace.name or ""
        is_b = name.startswith("B ")
        is_injected = "inject" in name.lower() or "synthetic" in name.lower()
        color = YELLOW if is_b or is_injected else TEXT
        if trace.mode == "markers":
            trace.marker.color = color
            trace.marker.opacity = 0.72
            if "Prepared background" in name:
                trace.marker.symbol = "circle-open"
                trace.marker.size = 6
                trace.marker.opacity = 0.9
                trace.marker.line.width = 1.2
                trace.zorder = 2
                trace.legendrank = 1
                trace.name = "Prepared observations"
            elif name == "With synthetic injection":
                trace.marker.symbol = "circle"
                trace.marker.size = 3
                trace.marker.opacity = 0.85
                trace.zorder = 1
                trace.legendrank = 2
                trace.name = "Artificial injection"
            trace.hovertemplate = (
                "Time: %{x:.5f} days<br>Normalized flux: %{y:.7f}<extra>%{fullData.name}</extra>"
            )
        else:
            trace.line.color = color
    fig.update_annotations(font_color=TEXT)
    for shape in fig.layout.shapes or ():
        if shape.type == "rect":
            shape.fillcolor = YELLOW
            shape.opacity = 0.10
        else:
            shape.line.color = YELLOW
    return fig


def show_chart(figure: go.Figure, **kwargs) -> None:
    """Keep native Streamlit from replacing the explicit accessible chart palette."""
    st.plotly_chart(dark_figure(figure), theme=None, **kwargs)
