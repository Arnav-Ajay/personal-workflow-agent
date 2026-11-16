"""Static PNG trend charts for standalone HTML and future email attachments."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure


@dataclass(frozen=True)
class ChartSeries:
    label: str
    points: list[tuple[datetime, Decimal | None]]
    provisional: bool = False


def line_chart(series: list[ChartSeries], *, ylabel: str, xlabel: str | None = None) -> bytes | None:
    """Keep missing observations as gaps; omit charts with no numeric data."""
    series = [entry for entry in series if any(value is not None for _, value in entry.points)]
    if not series:
        return None
    figure = Figure(figsize=(8.4, 3.1), dpi=140, layout="constrained", facecolor="white")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    colors = ("#5279c4", "#12745e", "#b07839", "#8869a9", "#b44040", "#23889c")
    try:
        for index, entry in enumerate(series):
            points = sorted(entry.points, key=lambda point: point[0])
            values = []
            for _, value in points:
                if value is not None and not value.is_finite():
                    raise ValueError("Chart values must be finite")
                values.append(float(value) if value is not None else float("nan"))
            axis.plot([x for x, _ in points], values, label=entry.label,
                      color=colors[index % len(colors)], linewidth=1.8,
                      linestyle="--" if entry.provisional else "-",
                      marker="o" if len(points) <= 10 else None, markersize=4)
        axis.axhline(0, color="#bdc8d5", linewidth=0.7)
        axis.grid(axis="y", color="#edf0f3", linewidth=0.8)
        axis.spines[["top", "right"]].set_visible(False)
        axis.spines[["left", "bottom"]].set_color("#dce3e9")
        axis.tick_params(colors="#687787", labelsize=9)
        axis.set_ylabel(ylabel, color="#687787", fontsize=9)
        if xlabel:
            axis.set_xlabel(xlabel, color="#687787", fontsize=9)
        locator = AutoDateLocator(minticks=3, maxticks=6)
        axis.xaxis.set_major_locator(locator)
        axis.xaxis.set_major_formatter(ConciseDateFormatter(locator))
        axis.legend(frameon=False, fontsize=8, loc="best", ncols=min(2, len(series)))
        buffer = BytesIO()
        figure.savefig(buffer, format="png", metadata={"Software": "Personal Workflow Agent"})
        return buffer.getvalue()
    finally:
        figure.clear()
