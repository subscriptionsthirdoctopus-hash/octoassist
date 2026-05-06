"""Server-rendered inline-SVG chart helpers.

Tiny by design — these are illustrations of summary data, not interactive
visualisations. Output is a string of SVG markup safe to drop into a
Jinja template (with the `safe` filter).
"""
from __future__ import annotations

import math
from html import escape


# ----------  Brand palette ----------

NAVY  = "#11233f"
TEAL  = "#1aa6b7"
SLATE = "#5a6473"
LINE  = "#d8dee8"
GREEN = "#2f9e6b"
AMBER = "#c98a17"
RED   = "#c1432a"
BLUE  = "#1f4f9f"
PURPLE = "#5b3a99"

# Status colour mapping (matches the badge palette in styles.css)
STATUS_COLORS = {
    "open":          "#1f4f9f",
    "in_progress":   "#8a5b00",
    "on_hold":       "#5b3a99",
    "resolved":      "#1f6e44",
    "closed":        "#5a6473",
    "cancelled":     "#8a3019",
    # change/problem extras
    "draft":         "#5a6473",
    "under_review":  "#8a5b00",
    "approved":      "#1f6e44",
    "rejected":      "#8a3019",
    "completed":     "#1f6e44",
    "failed":        "#8a3019",
    "investigating": "#8a5b00",
    "root_cause_identified": "#5b3a99",
    "workaround_in_place":   "#5b3a99",
}

PRIORITY_COLORS = {
    "low":      "#5a6473",
    "medium":   "#1f4f9f",
    "high":     "#c98a17",
    "critical": "#c1432a",
}

DEFAULT_PALETTE = [TEAL, NAVY, AMBER, GREEN, PURPLE, RED, BLUE, "#7c5cff", "#3aa6f0"]


# ----------  Helpers ----------

def _fmt(n: float) -> str:
    return f"{n:.1f}".rstrip("0").rstrip(".") if isinstance(n, float) else str(n)


def _color_for(label: str, palette_idx: int = 0) -> str:
    if label in STATUS_COLORS:
        return STATUS_COLORS[label]
    if label in PRIORITY_COLORS:
        return PRIORITY_COLORS[label]
    return DEFAULT_PALETTE[palette_idx % len(DEFAULT_PALETTE)]


# ----------  Donut chart ----------

def donut(
    data: list[tuple[str, int]],
    *,
    size: int = 180,
    thickness: int = 22,
    show_legend: bool = True,
) -> str:
    """Render a donut chart from `data = [(label, count), ...]`.

    Returns the wrapping HTML — the donut SVG plus a flex legend below it.
    """
    total = sum(c for _, c in data)
    if total <= 0:
        return f'<div class="chart-empty" style="height:{size}px;display:flex;align-items:center;justify-content:center;color:{SLATE};font-size:13px;">No data</div>'

    cx = cy = size / 2
    radius = (size - thickness) / 2
    circumference = 2 * math.pi * radius

    segments = []
    cumulative = 0.0
    legend_items = []
    for idx, (label, count) in enumerate(data):
        if count <= 0:
            continue
        color = _color_for(label, idx)
        length = (count / total) * circumference
        offset = -cumulative
        segments.append(
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
            f'stroke="{color}" stroke-width="{thickness}" '
            f'stroke-dasharray="{length:.2f} {circumference - length:.2f}" '
            f'stroke-dashoffset="{offset:.2f}" '
            f'transform="rotate(-90 {cx} {cy})" '
            f'><title>{escape(label)}: {count}</title></circle>'
        )
        cumulative += length
        pct = round(100 * count / total)
        legend_items.append(
            f'<li><span class="dot" style="background:{color}"></span>'
            f'<span class="lbl">{escape(label.replace("_", " "))}</span>'
            f'<span class="val">{count} <span style="color:{SLATE}">({pct}%)</span></span></li>'
        )

    centre_text = (
        f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" font-size="22" font-weight="700" fill="{NAVY}">{total}</text>'
        f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="10" fill="{SLATE}" letter-spacing="1">TOTAL</text>'
    )

    legend = (
        f'<ul class="chart-legend">{"".join(legend_items)}</ul>'
        if show_legend else ""
    )

    return (
        f'<div class="chart-donut">'
        f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
        f'{"".join(segments)}{centre_text}'
        f'</svg>{legend}</div>'
    )


# ----------  Horizontal bar chart ----------

def bars_h(
    data: list[tuple[str, int]],
    *,
    width: int = 480,
    bar_height: int = 22,
    label_w: int = 160,
    show_value: bool = True,
) -> str:
    if not data:
        return f'<div class="chart-empty" style="color:{SLATE};font-size:13px;padding:24px;">No data</div>'
    max_val = max((c for _, c in data), default=0) or 1
    height = len(data) * (bar_height + 6) + 4
    bar_max = width - label_w - 60

    rows: list[str] = []
    for i, (label, count) in enumerate(data):
        y = i * (bar_height + 6)
        bar_w = (count / max_val) * bar_max
        color = _color_for(label, i)
        rows.append(
            f'<text x="0" y="{y + bar_height/2 + 4}" font-size="12" fill="{NAVY}">{escape(str(label))}</text>'
            f'<rect x="{label_w}" y="{y + 4}" width="{max(bar_w, 1.5):.1f}" height="{bar_height - 8}" rx="3" fill="{color}"/>'
        )
        if show_value:
            rows.append(
                f'<text x="{label_w + bar_w + 6}" y="{y + bar_height/2 + 4}" '
                f'font-size="12" fill="{NAVY}" font-weight="600">{count}</text>'
            )
    return f'<svg class="chart-bars" viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMinYMin meet">{"".join(rows)}</svg>'


# ----------  Sparkline (tiny line chart for KPI cards) ----------

def sparkline(values: list[float], *, width: int = 140, height: int = 36) -> str:
    if not values:
        return ""
    pts = []
    fill_pts = []
    max_v = max(values) or 1
    min_v = min(values) if min(values) >= 0 else 0
    span = max_v - min_v or 1
    n = len(values)
    for i, v in enumerate(values):
        x = (i / max(1, n - 1)) * (width - 2) + 1
        y = height - 2 - ((v - min_v) / span) * (height - 4)
        pts.append(f"{x:.2f},{y:.2f}")
        fill_pts.append((x, y))
    polyline = " ".join(pts)
    fill_path = (
        f"M {fill_pts[0][0]:.2f} {height} "
        + " ".join(f"L {x:.2f} {y:.2f}" for x, y in fill_pts)
        + f" L {fill_pts[-1][0]:.2f} {height} Z"
    )
    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
        f'<path d="{fill_path}" fill="{TEAL}" fill-opacity="0.12"/>'
        f'<polyline points="{polyline}" fill="none" stroke="{TEAL}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )


# ----------  Time-series line ----------

def line_series(
    points: list[tuple[str, float]],
    *,
    width: int = 720,
    height: int = 200,
    y_label: str = "",
) -> str:
    if not points:
        return f'<div class="chart-empty" style="color:{SLATE};font-size:13px;padding:24px;">No data</div>'
    pad_l, pad_r, pad_t, pad_b = 38, 8, 12, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    values = [v for _, v in points]
    max_v = max(values) or 1

    # Y-axis grid lines (at 0, 25, 50, 75, 100% of max)
    grid = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = pad_t + (1 - frac) * plot_h
        v = max_v * frac
        grid.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" y2="{y:.1f}" stroke="{LINE}" stroke-width="1"/>'
            f'<text x="{pad_l - 4}" y="{y + 3:.1f}" font-size="10" fill="{SLATE}" text-anchor="end">{int(round(v))}</text>'
        )

    n = len(points)
    pts = []
    for i, (lbl, v) in enumerate(points):
        x = pad_l + (i / max(1, n - 1)) * plot_w
        y = pad_t + (1 - (v / max_v if max_v else 0)) * plot_h
        pts.append((x, y, lbl, v))

    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in pts)
    fill_path = (
        f"M {pts[0][0]:.1f} {pad_t + plot_h} "
        + " ".join(f"L {x:.1f} {y:.1f}" for x, y, _, _ in pts)
        + f" L {pts[-1][0]:.1f} {pad_t + plot_h} Z"
    )

    # X-axis labels (sparse — first, mid, last + every 7th)
    x_labels = []
    show_idxs = {0, n // 4, n // 2, (3 * n) // 4, n - 1}
    for idx in sorted(show_idxs):
        if idx >= n:
            continue
        x, _, lbl, _ = pts[idx]
        x_labels.append(
            f'<text x="{x:.1f}" y="{pad_t + plot_h + 16}" font-size="10" fill="{SLATE}" text-anchor="middle">{escape(lbl)}</text>'
        )

    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="{TEAL}"><title>{escape(lbl)}: {int(v)}</title></circle>'
        for x, y, lbl, v in pts
    )

    return (
        f'<svg class="chart-line" viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMinYMin meet">'
        f'{"".join(grid)}'
        f'<path d="{fill_path}" fill="{TEAL}" fill-opacity="0.12"/>'
        f'<polyline points="{polyline}" fill="none" stroke="{TEAL}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        f'{dots}'
        f'{"".join(x_labels)}'
        f'</svg>'
    )
