"""Server-rendered inline-SVG chart helpers.

Tiny by design — these are illustrations of summary data, not interactive
visualisations. Output is a string of SVG markup safe to drop into a
Jinja template (with the `safe` filter).
"""
from __future__ import annotations

import math
from html import escape


# ----------  Brand palette (dark-mode tuned) ----------
# These are used for SVG fills/strokes inside server-rendered charts. The
# constants are dark-mode safe — labels/axis ticks read on a dark surface,
# data colours stay vivid. (Light-on-light or dark-on-dark would be invisible.)

NAVY  = "#cbd5e1"   # slate-300 — used for chart text labels (renamed-in-spirit)
TEAL  = "#3b82f6"   # accent blue — primary data colour
SLATE = "#94a3b8"   # slate-400 — axis ticks, captions, "(N%)" suffixes
LINE  = "#2e3a55"   # subtle dark border — grid lines on plots
GREEN = "#22c55e"
AMBER = "#f59e0b"
RED   = "#ef4444"
BLUE  = "#3b82f6"
PURPLE = "#a78bfa"

# Status colour mapping — dark-mode tuned to match the .status pill palette
# in styles.css. Bright, saturated chips that sing on the dark surface.
STATUS_COLORS = {
    "open":          "#3b82f6",
    "in_progress":   "#f59e0b",
    "on_hold":       "#a78bfa",
    "resolved":      "#22c55e",
    "closed":        "#94a3b8",
    "cancelled":     "#ef4444",
    # change/problem extras
    "draft":         "#94a3b8",
    "under_review":  "#f59e0b",
    "approved":      "#22c55e",
    "rejected":      "#ef4444",
    "completed":     "#22c55e",
    "failed":        "#ef4444",
    "investigating": "#f59e0b",
    "root_cause_identified": "#a78bfa",
    "workaround_in_place":   "#a78bfa",
}

PRIORITY_COLORS = {
    "low":      "#94a3b8",
    "medium":   "#3b82f6",
    "high":     "#f59e0b",
    "critical": "#ef4444",
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

    # Dark-mode safe: slate-100 for the big number, slate-400 for the 'TOTAL' label.
    centre_text = (
        f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" font-size="22" font-weight="700" fill="#f1f5f9">{total}</text>'
        f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="10" fill="#94a3b8" letter-spacing="1">TOTAL</text>'
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
    """Horizontal bar chart.

    Layout: [label column] [bar area] [value column].
    - Labels are right-truncated with an ellipsis when they exceed label_w
      (rough 6.5px-per-char heuristic for the 12px sans font we use).
    - Value column is reserved at the right edge so the count never
      overlaps the bar or the label.
    - Text fill defaults to slate-200 — readable on a dark surface.
    """
    if not data:
        return f'<div class="chart-empty" style="color:#94a3b8;font-size:13px;padding:24px;">No data</div>'
    max_val = max((c for _, c in data), default=0) or 1
    height = len(data) * (bar_height + 6) + 4
    VALUE_COL_W = 44                              # reserved on the right for the count
    bar_x = label_w + 8                           # 8px gap between label and bar
    bar_max = max(40, width - label_w - VALUE_COL_W - 12)
    value_x = bar_x + bar_max + 8

    # Truncate labels that won't fit in the label column at 12px sans.
    def _truncate(text: str) -> str:
        max_chars = max(8, int(label_w / 6.5))
        s = str(text or "")
        return s if len(s) <= max_chars else s[: max_chars - 1].rstrip() + "…"

    # Dark-mode safe text colors
    LABEL_FILL = "#cbd5e1"   # slate-300
    VALUE_FILL = "#f1f5f9"   # slate-100, slightly brighter for the count

    rows: list[str] = []
    for i, (label, count) in enumerate(data):
        y = i * (bar_height + 6)
        bar_w = (count / max_val) * bar_max
        color = _color_for(label, i)
        truncated = _truncate(label)
        rows.append(
            f'<text x="0" y="{y + bar_height/2 + 4}" font-size="12" fill="{LABEL_FILL}">'
            f'<title>{escape(str(label))}</title>{escape(truncated)}</text>'
            f'<rect x="{bar_x}" y="{y + 4}" width="{max(bar_w, 1.5):.1f}" height="{bar_height - 8}" rx="3" fill="{color}"/>'
        )
        if show_value:
            rows.append(
                f'<text x="{value_x}" y="{y + bar_height/2 + 4}" '
                f'font-size="12" fill="{VALUE_FILL}" font-weight="700">{count}</text>'
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
