"""Lay the model out and draw it as an SVG string."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from fractions import Fraction
from html import escape
from typing import TYPE_CHECKING, Any

from .model import Arrow, Clock, Gap, Marker, Measure, Note, Region, Row, Signal
from .themes import THEMES
from .units import TIME_UNITS, format_time

if TYPE_CHECKING:
    from .diagram import Diagram


SANS = (
    "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Hiragino Sans GB', "
    "'Noto Sans CJK SC', 'Microsoft YaHei', Helvetica, Arial, sans-serif"
)
MONO = (
    "'SF Mono', 'JetBrains Mono', Menlo, Consolas, 'DejaVu Sans Mono', "
    "'PingFang SC', 'Hiragino Sans GB', 'Noto Sans CJK SC', monospace"
)

PAD = 20
TITLE_H = 34
MARKER_BAND = 26
ROW_H = 40
WAVE_TOP = 13  # within a row
WAVE_BOT = 31
GROUP_H = 26
INDENT = 14
LANE_H = 26
AXIS_H = 32
GAP_W = 20
LABEL_SIZE = 12.5
VALUE_SIZE = 11
CAPTION_SIZE = 11
CAPTION_LINE_H = 18
CAPTION_TOP_GAP = 10

_WIDE_SYMBOLS = frozenset("←→↑↓↔⇒⇐…—–")


def text_width(s: str, size: float, mono: bool = False) -> float:
    w = 0.0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            w += size * 1.04
        elif ch in _WIDE_SYMBOLS:
            w += size * 0.95
        elif ch == " ":
            w += size * 0.34
        elif mono:
            w += size * 0.61
        else:
            w += size * (0.63 if ch.isupper() or ch.isdigit() else 0.55)
    return w


def fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


class TimeMap:
    """Time -> x, with every gap squeezed into GAP_W pixels."""

    def __init__(self, x0: float, scale: float, gaps: list[Gap], end: Fraction):
        self.x0 = x0
        self.scale = scale  # pixels per second
        self.gaps = gaps
        self.end = end

    def x(self, t: Fraction) -> float:
        acc, prev = self.x0, Fraction(0)
        for g in self.gaps:
            if t <= g.start:
                break
            acc += float(g.start - prev) * self.scale
            if t < g.end:
                return acc + float((t - g.start) / (g.end - g.start)) * GAP_W
            acc += GAP_W
            prev = g.end
        return acc + float(t - prev) * self.scale

    def visible(self) -> list[tuple[Fraction, Fraction]]:
        out, prev = [], Fraction(0)
        for g in self.gaps:
            if g.start > prev:
                out.append((prev, g.start))
            prev = g.end
        if self.end > prev:
            out.append((prev, self.end))
        return out


def _merge_gaps(gaps: list[Gap], end: Fraction) -> list[Gap]:
    out: list[Gap] = []
    for g in sorted(gaps, key=lambda g: g.start):
        if g.start >= end:
            continue
        g = Gap(g.start, min(g.end, end))
        if out and g.start <= out[-1].end:
            out[-1] = Gap(out[-1].start, max(out[-1].end, g.end))
        else:
            out.append(g)
    return out


def _max_visible_span(t0: Fraction, t1: Fraction, gaps: list[Gap]) -> Fraction:
    cur = t0
    best = Fraction(0)
    for g in gaps:
        if g.end <= cur:
            continue
        if g.start >= t1:
            break
        if g.start > cur:
            best = max(best, g.start - cur)
        cur = max(cur, g.end)
    if t1 > cur:
        best = max(best, t1 - cur)
    return best


@dataclass
class RenderStats:
    width: float = 0.0
    height: float = 0.0
    px_per_ns: float = 0.0
    hit_width_cap: bool = False
    truncated_labels: list[dict[str, Any]] = field(default_factory=list)
    dropped_labels: list[dict[str, Any]] = field(default_factory=list)
    crowded_arrows: list[dict[str, Any]] = field(default_factory=list)
    crowded_notes: list[dict[str, Any]] = field(default_factory=list)


def _scale_with_cap(d: Diagram, visible_span: Fraction, gaps: list[Gap], end: Fraction) -> tuple[float, bool]:
    if d.px_per_ns:
        return d.px_per_ns * 1e9, False
    clocks = [r for r in d.rows if isinstance(r, Clock)]
    span = float(visible_span) or 1e-9
    if not clocks:
        scale, floor = 900 / span, 0.0
    else:
        half = float(min(min(c.duty, 1 - c.duty) * c.period for c in clocks))
        scale, floor = 16 / half, 5 / half
    # Widen until every bus value fits in its own visible sub-segment.
    for r in d.rows:
        if isinstance(r, Signal) and r.kind == "bus":
            for t0, t1, v in r.segments(end):
                if v in ("x", "z"):
                    continue
                vis = _max_visible_span(t0, t1, gaps)
                if vis <= 0:
                    continue
                need = text_width(r.display(v), VALUE_SIZE, mono=True) + 18
                scale = max(scale, need / float(vis))
    hit_cap = False
    if span * scale > 2400:
        scale = max(2400 / span, floor)
        hit_cap = True
    return scale, hit_cap


def _scale(d: Diagram, visible_span: Fraction, gaps: list[Gap], end: Fraction) -> float:
    return _scale_with_cap(d, visible_span, gaps, end)[0]


def _nice_step(raw: float) -> float:
    exp = math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * 10**exp >= raw:
            return m * 10**exp
    return 10 ** (exp + 1)


@dataclass
class RowBox:
    row: Row
    top: float

    @property
    def hi(self) -> float:
        return self.top + WAVE_TOP

    @property
    def lo(self) -> float:
        return self.top + WAVE_BOT

    @property
    def mid(self) -> float:
        return self.top + (WAVE_TOP + WAVE_BOT) / 2


class Canvas:
    def __init__(self, theme: dict[str, str], pfx: str = ""):
        self.c = theme
        self.pfx = pfx
        self.parts: list[str] = []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def url(self, name: str) -> str:
        return f"url(#{self.pfx}{name})"

    def line(self, x1, y1, x2, y2, color, width=1.5, dash=None, opacity=None, cap=None):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        extra += f' stroke-opacity="{opacity}"' if opacity is not None else ""
        extra += f' stroke-linecap="{cap}"' if cap else ""
        self.add(
            f'<line x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(x2)}" y2="{fmt(y2)}" '
            f'stroke="{color}" stroke-width="{width}"{extra}/>'
        )

    def text(
        self,
        x,
        y,
        s,
        color,
        size=LABEL_SIZE,
        anchor="start",
        mono=False,
        weight=None,
        bg=False,
        bg_fill=None,
        bg_stroke=None,
        pad_x=5.5,
    ):
        if bg:
            w = text_width(s, size, mono) + pad_x * 2
            left = x - w / 2 if anchor == "middle" else x - pad_x if anchor == "start" else x - w + pad_x
            fill = bg_fill or self.c["badge_bg"]
            stroke_attr = (
                f' stroke="{bg_stroke}" stroke-width="0.9" stroke-opacity="0.32"' if bg_stroke else ""
            )
            self.add(
                f'<rect x="{fmt(left)}" y="{fmt(y - size * 0.84)}" width="{fmt(w)}" '
                f'height="{fmt(size * 1.34)}" rx="4" fill="{fill}" fill-opacity="0.92"{stroke_attr}/>'
            )
        family = MONO if mono else SANS
        extra = f' font-weight="{weight}"' if weight else ""
        self.add(
            f'<text x="{fmt(x)}" y="{fmt(y)}" fill="{color}" font-size="{size}" '
            f'font-family="{family}" text-anchor="{anchor}"{extra}>{escape(s)}</text>'
        )


def _clamp_cx(cx: float, half_w: float, x0: float, x_end: float) -> float:
    if x_end - x0 >= half_w * 2 + 4:
        return min(max(cx, x0 + half_w + 2), x_end - half_w - 2)
    return cx


def _wave_segments(cx: float, top: float, bottom: float, amp: float = 2.5) -> list[tuple[float, float, float, float, float, float]]:
    """Build smooth C1-continuous sinusoidal cubic Bezier segments from top to bottom."""
    h = bottom - top
    n = max(4, round(h / 34))
    dy = h / n
    segs = []
    for i in range(n):
        ya = top + i * dy
        yb = top + (i + 1) * dy
        d = amp if i % 2 == 0 else -amp
        segs.append((cx + d, ya + dy * 0.35, cx + d, yb - dy * 0.35, cx, yb))
    return segs


def _bezier_path(cx: float, top: float, bottom: float, amp: float = 2.5) -> str:
    segs = _wave_segments(cx, top, bottom, amp)
    parts = [f"M{fmt(cx)},{fmt(top)}"]
    for c1x, c1y, c2x, c2y, ex, ey in segs:
        parts.append(f"C{fmt(c1x)},{fmt(c1y)} {fmt(c2x)},{fmt(c2y)} {fmt(ex)},{fmt(ey)}")
    return " ".join(parts)


def _ribbon_path(cx1: float, cx2: float, top: float, bottom: float, amp: float = 2.5) -> str:
    """Closed mask ribbon bounded by two contoured S-curves."""
    segs1 = _wave_segments(cx1, top, bottom, amp)
    segs2 = _wave_segments(cx2, top, bottom, amp)
    parts = [f"M{fmt(cx1)},{fmt(top)}"]
    for c1x, c1y, c2x, c2y, ex, ey in segs1:
        parts.append(f"C{fmt(c1x)},{fmt(c1y)} {fmt(c2x)},{fmt(c2y)} {fmt(ex)},{fmt(ey)}")
    parts.append(f"L{fmt(cx2)},{fmt(bottom)}")
    # Walk segs2 backwards from bottom to top.
    n = len(segs2)
    h = bottom - top
    dy = h / n
    for i in range(n - 1, -1, -1):
        ya = top + i * dy
        yb = top + (i + 1) * dy
        d = amp if i % 2 == 0 else -amp
        parts.append(f"C{fmt(cx2 + d)},{fmt(yb - dy * 0.35)} {fmt(cx2 + d)},{fmt(ya + dy * 0.35)} {fmt(cx2)},{fmt(ya)}")
    parts.append("Z")
    return " ".join(parts)


def _is_dark(theme: dict[str, str]) -> bool:
    r, g, b = (int(theme["bg"][i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128


def _bezier_pt(
    x1: float,
    y1: float,
    c1: tuple[float, float],
    c2: tuple[float, float],
    x2: float,
    y2: float,
    t: float,
) -> tuple[float, float]:
    u = 1.0 - t
    px = u**3 * x1 + 3 * u**2 * t * c1[0] + 3 * u * t**2 * c2[0] + t**3 * x2
    py = u**3 * y1 + 3 * u**2 * t * c1[1] + 3 * u * t**2 * c2[1] + t**3 * y2
    return px, py


Box2D = tuple[float, float, float, float]  # cx, cy, half_w, half_h


def _collides(cx: float, cy: float, hw: float, hh: float, obstacles: list[Box2D]) -> int:
    hits = 0
    for ox, oy, ohw, ohh in obstacles:
        if abs(cx - ox) < hw + ohw + 4 and abs(cy - oy) < hh + ohh + 3:
            hits += 1
    return hits


def render(d: Diagram) -> str:
    return render_with_stats(d)[0]


def render_with_stats(d: Diagram) -> tuple[str, RenderStats]:
    c = THEMES[d.theme]
    is_dark = _is_dark(c)
    end = d.end
    gaps = _merge_gaps(d.gaps, end)
    visible_span = end - sum((g.end - g.start for g in gaps), Fraction(0))
    scale, hit_cap = _scale_with_cap(d, visible_span, gaps, end)
    stats = RenderStats(px_per_ns=scale / 1e9, hit_width_cap=hit_cap)

    markers = [a for a in d.annotations if isinstance(a, Marker) and Fraction(0) <= a.time <= end]
    measures = [a for a in d.annotations if isinstance(a, Measure) and a.end > 0 and a.start < end]
    arrows = [
        a
        for a in d.annotations
        if isinstance(a, Arrow)
        and Fraction(0) <= a.src.time <= end
        and Fraction(0) <= a.dst.time <= end
    ]
    regions = [a for a in d.annotations if isinstance(a, Region) and a.end > 0 and a.start < end]
    notes = [a for a in d.annotations if isinstance(a, Note) and Fraction(0) <= a.time <= end]

    # ---- horizontal time mapping & left label width ----
    label_w = 0.0
    open_names: list[str] = []
    for row in d.rows:
        common = 0
        while common < len(open_names) and common < len(row.groups) and open_names[common] == row.groups[common]:
            common += 1
        del open_names[common:]
        for depth in range(common, len(row.groups)):
            gname = row.groups[depth]
            label_w = max(label_w, depth * INDENT + text_width(gname, 12))
            open_names.append(gname)
        is_ascii = all(ord(ch) < 128 for ch in row.label)
        label_w = max(label_w, len(row.groups) * INDENT + text_width(row.label, LABEL_SIZE, mono=is_ascii))

    x0 = PAD + label_w + 22
    tm = TimeMap(x0, scale, gaps, end)
    x_end = tm.x(end)

    # ---- pack marker badges into lanes (prevent horizontal collisions) ----
    marker_items: list[tuple[Marker, float, int]] = []
    marker_lane_rights: list[float] = []
    marker_max_right = 0.0
    for m in sorted(markers, key=lambda mk: tm.x(mk.time)):
        x = tm.x(m.time)
        if not m.label:
            marker_items.append((m, x, 0))
            continue
        half_w = text_width(m.label, 10.5) / 2 + 5.5
        cx = max(x, 6.0 + half_w)
        lane_idx = 0
        while lane_idx < len(marker_lane_rights):
            if marker_lane_rights[lane_idx] + 6 <= cx - half_w:
                break
            lane_idx += 1
        if lane_idx == len(marker_lane_rights):
            marker_lane_rights.append(cx + half_w)
        else:
            marker_lane_rights[lane_idx] = cx + half_w
        marker_max_right = max(marker_max_right, cx + half_w)
        marker_items.append((m, cx, lane_idx))
    marker_band_h = (MARKER_BAND + max(0, len(marker_lane_rights) - 1) * 20) if marker_lane_rights else 0

    # ---- pack non-overlapping measures into shared horizontal lanes ----
    measure_lanes: list[list[tuple[float, float]]] = []
    measure_items: list[tuple[Measure, int, float, float, float, str, bool]] = []
    for m in measures:
        x1 = tm.x(max(m.start, Fraction(0)))
        x2 = tm.x(min(m.end, end))
        label = m.label or format_time(m.end - m.start, d.timescale)
        half_w = text_width(label, 10.5) / 2 + 5.5
        inline = (x2 - x1) >= text_width(label, 10.5) + 32
        cx = _clamp_cx((x1 + x2) / 2, half_w, x0, x_end)
        occ_l = min(x1, cx - half_w)
        occ_r = max(x2, cx + half_w)
        lane_idx = 0
        while lane_idx < len(measure_lanes):
            if all(occ_r + 8 <= la or occ_l - 8 >= lb for la, lb in measure_lanes[lane_idx]):
                break
            lane_idx += 1
        if lane_idx == len(measure_lanes):
            measure_lanes.append([])
        measure_lanes[lane_idx].append((occ_l, occ_r))
        measure_items.append((m, lane_idx, x1, x2, cx, label, inline))

    # ---- vertical layout: title, marker band, group headers and rows ----
    y = PAD
    title_y = None
    if d.title:
        title_y = y + 19
        y += TITLE_H
    y += marker_band_h
    if d.rows and any(a.src.signal is a.dst.signal and a.src.signal is d.rows[0] for a in arrows):
        y += 14
    if d.rows and not d.rows[0].groups and any(n.row is d.rows[0] for n in notes):
        y += 18
    rows_top = y

    boxes: list[RowBox] = []
    headers: list[tuple[str, int, float]] = []  # name, depth, top
    brackets: list[tuple[int, float, float]] = []  # depth, top, bottom
    open_groups: list[tuple[str, float]] = []
    for row in d.rows:
        common = 0
        while (
            common < len(open_groups)
            and common < len(row.groups)
            and open_groups[common][0] == row.groups[common]
        ):
            common += 1
        while len(open_groups) > common:
            _, top = open_groups.pop()
            brackets.append((len(open_groups), top, y))
        for depth in range(common, len(row.groups)):
            name = row.groups[depth]
            headers.append((name, depth, y))
            open_groups.append((name, y + GROUP_H))
            y += GROUP_H
        boxes.append(RowBox(row, y))
        y += ROW_H
    while open_groups:
        _, top = open_groups.pop()
        brackets.append((len(open_groups), top, y))
    rows_bottom = y

    lanes_top = rows_bottom + 8
    axis_y = lanes_top + len(measure_lanes) * LANE_H + 8
    height = axis_y + AXIS_H + PAD

    captions_top: float | None = None
    caption_right = 0.0
    if d.captions:
        captions_top = axis_y + AXIS_H + CAPTION_TOP_GAP
        height = captions_top + len(d.captions) * CAPTION_LINE_H + PAD - 4
        caption_right = max(PAD + text_width(line, CAPTION_SIZE) + PAD for line in d.captions)

    note_min_right = max(x0 + text_width(n.label, 10.5) + 16 + PAD for n in notes) if notes else 0.0
    title_right = (PAD + text_width(d.title, 15.5) + PAD) if d.title else 0.0
    width = max(
        x_end + PAD + 26,
        title_right,
        marker_max_right + PAD if marker_max_right else 0.0,
        caption_right,
        note_min_right,
    )
    stats.width = round(width, 2)
    stats.height = round(height, 2)

    cv = Canvas(c, pfx=f"wv-{d.theme}-")
    by_row = {id(b.row): b for b in boxes}

    # ---- background, title ----
    cv.add(f'<rect width="100%" height="100%" fill="{c["bg"]}"/>')
    if title_y is not None:
        cv.text(PAD, title_y, d.title, c["title"], size=15.5, weight="600")

    # ---- viewport backdrop & alternating row stripes ----
    if boxes:
        cv.add(
            f'<rect x="{fmt(x0)}" y="{fmt(rows_top)}" width="{fmt(x_end - x0)}" '
            f'height="{fmt(rows_bottom - rows_top)}" rx="4" fill="{c["plot_bg"]}"/>'
        )
        for i, b in enumerate(boxes):
            if i % 2 == 1:
                stripe_x = PAD - 6 + len(b.row.groups) * INDENT
                cv.add(
                    f'<rect x="{fmt(stripe_x)}" y="{fmt(b.top + 1)}" width="{fmt(x_end - stripe_x)}" '
                    f'height="{fmt(ROW_H - 2)}" rx="4" fill="{c["row_alt"]}" fill-opacity="0.65"/>'
                )

    # ---- grid on the first clock's rising edges ----
    first_clock = next((r for r in d.rows if isinstance(r, Clock)), None)
    if first_clock is not None and boxes:
        if float(first_clock.period) * scale >= 10:
            for a, b in tm.visible():
                for t, kind in first_clock.edges_between(a - Fraction(1, 10**18), b):
                    if kind == "rise":
                        x = tm.x(t)
                        cv.line(x, rows_top, x, rows_bottom, c["grid"], width=1)

    if boxes:
        cv.line(x0, rows_top, x0, rows_bottom, c["border"], width=1, opacity=0.65)

    # ---- labels and group headers ----
    for name, depth, top in headers:
        cv.text(PAD + depth * INDENT, top + 17, name, c["group"], size=12, weight="600")
    for depth, top, bottom in brackets:
        bx = PAD + depth * INDENT + 3
        cv.add(
            f'<path d="M{fmt(bx + 4)},{fmt(top + 2)} H{fmt(bx)} V{fmt(bottom - 5)} H{fmt(bx + 4)}" '
            f'fill="none" stroke="{c["group"]}" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round" stroke-opacity="0.7"/>'
        )
    for b in boxes:
        is_ascii = all(ord(ch) < 128 for ch in b.row.label)
        cv.text(
            PAD + len(b.row.groups) * INDENT,
            b.mid + 4.5,
            b.row.label,
            c["text"],
            size=LABEL_SIZE,
            mono=is_ascii,
        )

    # ---- region boxes sit under the waves ----
    reg_fill_op = "0.11" if is_dark else "0.09"
    for a in regions:
        b = by_row[id(a.row)]
        x1, x2 = tm.x(max(a.start, Fraction(0))), tm.x(min(a.end, end))
        cv.add(
            f'<rect x="{fmt(x1)}" y="{fmt(b.hi - 3)}" width="{fmt(max(x2 - x1, 1))}" '
            f'height="{fmt(b.lo - b.hi + 6)}" rx="4" fill="{c["region"]}" fill-opacity="{reg_fill_op}" '
            f'stroke="{c["region"]}" stroke-width="1.1" stroke-opacity="0.58" stroke-dasharray="3.5 2.5"/>'
        )

    # ---- waves ----
    bus_labels: list[Box2D] = []
    for b in boxes:
        if isinstance(b.row, Clock):
            _draw_clock(cv, b, b.row, tm, end)
        elif b.row.kind == "wire":
            _draw_wire(cv, b, b.row, tm, end)
        else:
            _draw_bus(cv, b, b.row, tm, end, bus_labels, d.timescale, stats)

    # ---- region labels sit cleanly above the waves ----
    region_labels: list[Box2D] = []
    for a in regions:
        if a.label:
            b = by_row[id(a.row)]
            x1, x2 = tm.x(max(a.start, Fraction(0))), tm.x(min(a.end, end))
            hw = text_width(a.label, 10) / 2
            cx = _clamp_cx((x1 + x2) / 2, hw, x0, x_end)
            cy = b.hi - 6.5
            cv.text(cx, cy, a.label, c["region_text"], size=10, anchor="middle", weight="600")
            region_labels.append((cx, cy - 3.5, hw, 6.5))

    # ---- axis line, then contoured S-curve gaps cover waves and axis ----
    cv.line(x0, axis_y, x_end, axis_y, c["border"], width=1.1)
    for g in gaps:
        gx = tm.x(g.start)
        top, bottom = rows_top - 2, axis_y + 6
        cx1, cx2 = gx + 6.5, gx + 13.5
        cv.add(f'<path d="{_ribbon_path(cx1, cx2, top, bottom)}" fill="{c["bg"]}"/>')
        for cx in (cx1, cx2):
            cv.add(
                f'<path d="{_bezier_path(cx, top, bottom)}" fill="none" stroke="{c["muted"]}" '
                f'stroke-width="1.3" stroke-linecap="round" stroke-opacity="0.75"/>'
            )

    # ---- axis ticks ----
    unit = TIME_UNITS[d.timescale]
    step = _nice_step(40 / scale / float(unit)) * float(unit)
    step_f = Fraction(str(step))
    step_in_unit = float(step_f / unit)
    tick_digits = max(3, -math.floor(math.log10(step_in_unit)) + 1) if step_in_unit > 0 else 3
    for a, b in tm.visible():
        k = math.ceil(a / step_f)
        while (t := k * step_f) <= b:
            x = tm.x(t)
            if not any(abs(x - tm.x(g.start)) < 14 or abs(x - tm.x(g.end)) < 14 for g in gaps) or t == a:
                cv.line(x, axis_y, x, axis_y + 4, c["muted"], width=1)
                label = format_time(t, d.timescale, digits=tick_digits)[: -len(d.timescale)]
                cv.text(x, axis_y + 16, label, c["muted"], size=10, anchor="middle", mono=True)
            k += 1
    cv.text(x_end + 8, axis_y + 4, d.timescale, c["muted"], size=10, mono=True)

    # ---- markers ----
    marker_xs: list[float] = []
    for m, cx, lane_idx in marker_items:
        x = tm.x(m.time)
        marker_xs.append(x)
        ty = rows_top - 8 - lane_idx * 20
        line_top = min(rows_top - 3, ty + 4) if m.label else rows_top - 3
        cv.line(x, line_top, x, axis_y, c["marker"], width=1.1, dash="4 3", opacity=0.75)
        if m.label:
            cv.text(
                cx,
                ty,
                m.label,
                c["marker_text"],
                size=10.5,
                anchor="middle",
                weight="600",
                bg=True,
                bg_fill=c["marker_bg"],
                bg_stroke=c["marker_border"],
            )

    # ---- measures (non-overlapping ones share lanes) ----
    mh_url = cv.url("mh")
    for m, lane_idx, x1, x2, cx, label, inline in measure_items:
        ly = lanes_top + lane_idx * LANE_H + 16
        for x in (x1, x2):
            if any(abs(x - mx) <= 1.0 for mx in marker_xs):
                cv.line(x, rows_bottom + 2, x, ly + 4, c["measure"], width=1, dash="2 3", opacity=0.35)
            else:
                cv.line(x, rows_top, x, ly + 4, c["measure"], width=1, dash="2 3", opacity=0.25)
            cv.line(x, ly - 4, x, ly + 4, c["measure"], width=1.2, opacity=0.85, cap="round")
        cv.add(
            f'<line x1="{fmt(x1)}" y1="{fmt(ly)}" x2="{fmt(x2)}" y2="{fmt(ly)}" stroke="{c["measure"]}" '
            f'stroke-width="1.25" marker-start="{mh_url}" marker-end="{mh_url}"/>'
        )
        ty = ly + 3.8 if inline else ly - 6.5
        cv.text(
            cx,
            ty,
            label,
            c["measure"],
            size=10.5,
            anchor="middle",
            weight="500",
            bg=True,
            bg_fill=c["badge_bg"],
            bg_stroke=c["measure"],
        )

    # ---- arrows ----
    arrow_labels: list[Box2D] = []
    for a in arrows:
        _draw_arrow(cv, a, by_row, tm, x0, x_end, region_labels, bus_labels, arrow_labels, d.timescale, stats)

    # ---- single-point callout notes ----
    if notes:
        marker_labels: list[Box2D] = [
            (cx, (rows_top - 8 - lane_idx * 20) - 1.8, text_width(m.label, 10.5) / 2 + 5.5, 7.1)
            for m, cx, lane_idx in marker_items
            if m.label
        ]
        note_labels: list[Box2D] = []
        min_note_cy = (PAD + TITLE_H if d.title else PAD) + 5.5
        max_note_cy = max(min_note_cy, axis_y - 10.0)
        for n in notes:
            _draw_note(
                cv,
                n,
                by_row,
                tm,
                x0,
                x_end,
                min_note_cy,
                max_note_cy,
                region_labels,
                bus_labels,
                arrow_labels,
                marker_labels,
                note_labels,
                d.timescale,
                stats,
            )

    # ---- footer captions ----
    if captions_top is not None:
        cv.line(PAD, captions_top - 6, width - PAD, captions_top - 6, c["border"], width=1, opacity=0.5)
        for idx, line in enumerate(d.captions):
            cv.text(PAD, captions_top + idx * CAPTION_LINE_H + 11, line, c["marker_text"], size=CAPTION_SIZE)

    hi_top = "0.18" if is_dark else "0.12"
    bus_top = "0.18" if is_dark else "0.11"
    bus_bot = "0.07" if is_dark else "0.04"
    hatch_bg_op = "0.06" if is_dark else "0.04"
    hatch_line_op = "0.28" if is_dark else "0.22"

    p = cv.pfx
    defs = (
        "<defs>"
        f'<linearGradient id="{p}hi-grad" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{c["wave"]}" stop-opacity="{hi_top}"/>'
        f'<stop offset="100%" stop-color="{c["wave"]}" stop-opacity="0.01"/>'
        "</linearGradient>"
        f'<linearGradient id="{p}bus-grad" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{c["bus"]}" stop-opacity="{bus_top}"/>'
        f'<stop offset="100%" stop-color="{c["bus"]}" stop-opacity="{bus_bot}"/>'
        "</linearGradient>"
        f'<pattern id="{p}hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="6" height="6" fill="{c["x"]}" fill-opacity="{hatch_bg_op}"/>'
        f'<line x1="0" y1="0" x2="0" y2="6" stroke="{c["x"]}" stroke-width="1.05" stroke-opacity="{hatch_line_op}"/>'
        "</pattern>"
        f'<marker id="{p}ah" viewBox="0 0 10 10" refX="9.2" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto">'
        f'<path d="M0,1.6 L10,5 L0,8.4 L2.3,5 z" fill="{c["arrow"]}"/></marker>'
        f'<marker id="{p}mh" viewBox="0 0 10 10" refX="9.2" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">'
        f'<path d="M0,1.6 L10,5 L0,8.4 L2.3,5 z" fill="{c["measure"]}"/></marker>'
        "</defs>"
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(width)}" height="{fmt(height)}" '
        f'viewBox="0 0 {fmt(width)} {fmt(height)}">' + defs + "".join(cv.parts) + "</svg>\n"
    )
    return svg, stats


def _draw_clock(cv: Canvas, b: RowBox, clk: Clock, tm: TimeMap, d_end: Fraction) -> None:
    color = cv.c["clock"]
    paths = []
    chevrons = []
    show_chevrons = float(min(clk.duty, 1 - clk.duty) * clk.period) * tm.scale >= 12
    for a, end in tm.visible():
        level = clk.level_at(a)
        x_start = tm.x(a) - (GAP_W / 2 if a > 0 else 0.0)
        x_stop = tm.x(end) + (GAP_W / 2 if end < d_end else 0.0)
        p = [f"M{fmt(x_start)},{fmt(b.hi if level else b.lo)}"]
        for t, kind in clk.edges_between(a, end):
            level = 1 if kind == "rise" else 0
            tx = tm.x(t)
            p.append(f"H{fmt(tx)}V{fmt(b.hi if level else b.lo)}")
            if show_chevrons and kind == "rise" and t < d_end:
                chevrons.append(
                    f"M{fmt(tx - 2.1)},{fmt(b.mid + 1.8)} L{fmt(tx)},{fmt(b.mid - 2.2)} L{fmt(tx + 2.1)},{fmt(b.mid + 1.8)}"
                )
        p.append(f"H{fmt(x_stop)}")
        paths.append("".join(p))
    cv.add(
        f'<path d="{" ".join(paths)}" fill="none" stroke="{color}" stroke-width="1.5" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )
    if chevrons:
        cv.add(
            f'<path d="{" ".join(chevrons)}" fill="none" stroke="{color}" stroke-width="1.2" '
            'stroke-linecap="round" stroke-linejoin="round" stroke-opacity="0.8"/>'
        )


def _draw_wire(cv: Canvas, b: RowBox, sig: Signal, tm: TimeMap, end: Fraction) -> None:
    c = cv.c
    cv.line(tm.x(Fraction(0)), b.lo, tm.x(end), b.lo, c["rail"], width=1)

    hi_grad_url = cv.url("hi-grad")
    hatch_url = cv.url("hatch")
    segs = sig.segments(end)
    for t0, t1, v in segs:
        x1, x2 = tm.x(t0), tm.x(t1)
        if v == 1:
            cv.add(
                f'<rect x="{fmt(x1)}" y="{fmt(b.hi)}" width="{fmt(x2 - x1)}" height="{fmt(b.lo - b.hi)}" '
                f'fill="{hi_grad_url}" stroke="none"/>'
            )
        elif v == "x":
            cv.add(
                f'<rect x="{fmt(x1)}" y="{fmt(b.hi)}" width="{fmt(x2 - x1)}" height="{fmt(b.lo - b.hi)}" '
                f'fill="{hatch_url}" stroke="{c["x"]}" stroke-width="1.2" stroke-opacity="0.7"/>'
            )
        elif v == "z":
            cv.line(x1, b.mid, x2, b.mid, c["z"], width=1.5, dash="4.5 2.5")

    # Join consecutive 0/1 segments into continuous paths without drawing fake 0<->1 edges on "x".
    path_parts: list[str] = []
    for i, (t0, t1, v) in enumerate(segs):
        if v not in (0, 1):
            continue
        x1, x2 = tm.x(t0), tm.x(t1)
        y = b.hi if v == 1 else b.lo
        if not path_parts:
            if i > 0 and segs[i - 1][2] == "z":
                path_parts.append(f"M{fmt(x1)},{fmt(b.mid)} V{fmt(y)}")
            else:
                path_parts.append(f"M{fmt(x1)},{fmt(y)}")
        else:
            path_parts.append(f"V{fmt(y)}")
        path_parts.append(f"H{fmt(x2)}")

        next_v = segs[i + 1][2] if i + 1 < len(segs) else None
        if next_v not in (0, 1):
            if next_v == "z":
                path_parts.append(f"V{fmt(b.mid)}")
            cv.add(
                f'<path d="{" ".join(path_parts)}" fill="none" stroke="{c["wave"]}" '
                f'stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round"/>'
            )
            path_parts = []


def _draw_bus(
    cv: Canvas,
    b: RowBox,
    sig: Signal,
    tm: TimeMap,
    end: Fraction,
    bus_labels: list[Box2D],
    timescale: str = "ns",
    stats: RenderStats | None = None,
) -> None:
    c = cv.c
    hatch_url = cv.url("hatch")
    bus_grad_url = cv.url("bus-grad")
    segs = sig.segments(end)
    for i, (t0, t1, v) in enumerate(segs):
        x1, x2 = tm.x(t0), tm.x(t1)
        if v == "z":
            cv.line(x1, b.mid, x2, b.mid, c["z"], width=1.5, dash="4.5 2.5")
            continue
        s = min(4.5, (x2 - x1) / 2)
        sl = s if i > 0 else 0.0
        sr = s if t1 < end else 0.0
        pts = []
        if sl:
            pts.append((x1, b.mid))
        pts += [(x1 + sl, b.hi), (x2 - sr, b.hi)]
        if sr:
            pts.append((x2, b.mid))
        pts += [(x2 - sr, b.lo), (x1 + sl, b.lo)]
        poly = " ".join(f"{fmt(x)},{fmt(y)}" for x, y in pts)
        if v == "x":
            cv.add(
                f'<polygon points="{poly}" fill="{hatch_url}" stroke="{c["x"]}" '
                'stroke-opacity="0.7" stroke-width="1.2" stroke-linejoin="round"/>'
            )
            continue
        cv.add(
            f'<polygon points="{poly}" fill="{bus_grad_url}" stroke="{c["bus"]}" '
            'stroke-width="1.5" stroke-linejoin="round"/>'
        )
        # Put the value in the widest piece of the segment that no gap covers.
        pieces = [(max(t0, a), min(t1, e)) for a, e in tm.visible() if min(t1, e) > max(t0, a)]
        if not pieces:
            continue
        best = max(pieces, key=lambda p: tm.x(p[1]) - tm.x(p[0]))
        bx1, bx2 = tm.x(best[0]) + sl, tm.x(best[1]) - sr
        room = bx2 - bx1 - 6
        raw_label = sig.display(v)
        label = raw_label
        if room < VALUE_SIZE:
            if stats is not None:
                stats.dropped_labels.append(
                    {
                        "signal": sig.name,
                        "start": format_time(t0, timescale),
                        "end": format_time(t1, timescale),
                        "value": raw_label,
                        "reason": "too_narrow",
                    }
                )
            continue
        while label and text_width(label, VALUE_SIZE, mono=True) > room:
            label = label[:-2] + "…" if len(label) > 2 else ""
        if not label:
            if stats is not None:
                stats.dropped_labels.append(
                    {
                        "signal": sig.name,
                        "start": format_time(t0, timescale),
                        "end": format_time(t1, timescale),
                        "value": raw_label,
                        "reason": "too_narrow",
                    }
                )
            continue
        if label != raw_label and stats is not None:
            stats.truncated_labels.append(
                {
                    "signal": sig.name,
                    "start": format_time(t0, timescale),
                    "end": format_time(t1, timescale),
                    "value": raw_label,
                    "shown": label,
                }
            )
        cx = (bx1 + bx2) / 2
        cv.text(
            cx,
            b.mid + 4,
            label,
            c["bus_text"],
            size=VALUE_SIZE,
            anchor="middle",
            mono=True,
            weight="500",
        )
        bus_labels.append((cx, b.mid + 2.1, text_width(label, VALUE_SIZE, mono=True) / 2, 7.5))


def _draw_arrow(
    cv: Canvas,
    a: Arrow,
    by_row: dict[int, RowBox],
    tm: TimeMap,
    x0: float,
    x_end: float,
    region_labels: list[Box2D],
    bus_labels: list[Box2D],
    arrow_labels: list[Box2D],
    timescale: str = "ns",
    stats: RenderStats | None = None,
) -> None:
    c = cv.c
    b1, b2 = by_row[id(a.src.signal)], by_row[id(a.dst.signal)]
    x1, y1 = tm.x(a.src.time), b1.mid
    x2, y2 = tm.x(a.dst.time), b2.mid
    same_row = b1 is b2
    lift = 22.0
    if same_row:
        c1, c2 = (x1, y1 - lift), (x2, y2 - lift)
    else:
        dx = max(abs(x2 - x1), 30) * 0.5
        c1, c2 = (x1 + dx, y1), (x2 - dx, y2)

    mx, my = _bezier_pt(x1, y1, c1, c2, x2, y2, 0.5)
    if a.label:
        half_w = text_width(a.label, 10.5) / 2 + 5.5
        half_h = 7.1
        obstacles = region_labels + bus_labels + arrow_labels
        mx = _clamp_cx(mx, half_w, x0, x_end)

        if same_row:
            candidates = []
            for test_lift in (22.0, 32.0, 42.0):
                tc1, tc2 = (x1, y1 - test_lift), (x2, y2 - test_lift)
                for t in (0.5, 0.35, 0.65):
                    px, py = _bezier_pt(x1, y1, tc1, tc2, x2, y2, t)
                    px = _clamp_cx(px, half_w, x0, x_end)
                    candidates.append((px, py, tc1, tc2))
            best = min(
                candidates,
                key=lambda cand: _collides(cand[0], cand[1] + 2.0, half_w, half_h, obstacles),
            )
            mx, my, c1, c2 = best
        else:
            candidates_diff: list[tuple[float, float]] = []
            for t in (0.5, 0.35, 0.65, 0.25, 0.75):
                px, py = _bezier_pt(x1, y1, c1, c2, x2, y2, t)
                candidates_diff.append((_clamp_cx(px, half_w, x0, x_end), py))
            base_x, base_y = _bezier_pt(x1, y1, c1, c2, x2, y2, 0.5)
            base_x = _clamp_cx(base_x, half_w, x0, x_end)
            for dy in (-14.0, 14.0, -24.0, 24.0):
                candidates_diff.append((base_x, base_y + dy))
            mx, my = min(
                candidates_diff,
                key=lambda cand: _collides(cand[0], cand[1] + 2.0, half_w, half_h, obstacles),
            )

        best_hits = _collides(mx, my + 2.0, half_w, half_h, obstacles)
        if best_hits > 0 and stats is not None:
            stats.crowded_arrows.append(
                {
                    "src": f"{a.src.signal.name}@{format_time(a.src.time, timescale)}",
                    "dst": f"{a.dst.signal.name}@{format_time(a.dst.time, timescale)}",
                    "label": a.label,
                    "overlaps": best_hits,
                }
            )
        arrow_labels.append((mx, my + 2.0, half_w, half_h))

    cv.add(f'<circle cx="{fmt(x1)}" cy="{fmt(y1)}" r="5" fill="{c["arrow"]}" fill-opacity="0.2"/>')
    cv.add(f'<circle cx="{fmt(x1)}" cy="{fmt(y1)}" r="2.4" fill="{c["arrow"]}"/>')
    cv.add(
        f'<path d="M{fmt(x1)},{fmt(y1)} C{fmt(c1[0])},{fmt(c1[1])} {fmt(c2[0])},{fmt(c2[1])} '
        f'{fmt(x2)},{fmt(y2)}" fill="none" stroke="{c["arrow"]}" stroke-width="1.35" '
        f'stroke-linecap="round" marker-end="{cv.url("ah")}"/>'
    )
    if a.label:
        cv.text(
            mx,
            my + 3.8,
            a.label,
            c["arrow"],
            size=10.5,
            anchor="middle",
            weight="500",
            bg=True,
            bg_fill=c["badge_bg"],
            bg_stroke=c["arrow"],
        )


def _note_anchor_y(b: RowBox, n: Note) -> float:
    row = n.row
    if isinstance(row, Clock):
        if row._edge_kind_at(n.time) is not None:
            return b.mid
        return b.hi if row.level_at(n.time) == 1 else b.lo
    before = row.value_before(n.time)
    after = row.value_at(n.time)
    if n.time > 0 and (type(before) is not type(after) or before != after):
        return b.mid
    if row.kind == "wire":
        if after == 1:
            return b.hi
        if after == 0:
            return b.lo
    return b.mid


def _draw_note(
    cv: Canvas,
    n: Note,
    by_row: dict[int, RowBox],
    tm: TimeMap,
    x0: float,
    x_end: float,
    min_cy: float,
    max_cy: float,
    region_labels: list[Box2D],
    bus_labels: list[Box2D],
    arrow_labels: list[Box2D],
    marker_labels: list[Box2D],
    note_labels: list[Box2D],
    timescale: str = "ns",
    stats: RenderStats | None = None,
) -> None:
    c = cv.c
    col = c["note"]
    b = by_row[id(n.row)]
    ax = tm.x(n.time)
    ay = _note_anchor_y(b, n)

    half_w = text_width(n.label, 10.5) / 2 + 5.5
    half_h = 7.1
    obstacles = region_labels + bus_labels + arrow_labels + marker_labels + note_labels

    above = [(0.0, -21.0), (22.0, -21.0), (-22.0, -21.0), (32.0, -30.0), (-32.0, -30.0), (0.0, -32.0)]
    below = [(0.0, 19.0), (22.0, 19.0), (-22.0, 19.0), (32.0, 28.0), (-32.0, 28.0), (0.0, 30.0)]
    offsets = below + above if ay > b.mid else above + below

    candidates: list[tuple[float, float, int]] = []
    for idx, (dx, dy) in enumerate(offsets):
        cx = _clamp_cx(ax + dx, half_w, x0, x_end)
        cy = min(max(b.mid + dy, min_cy), max_cy)
        candidates.append((cx, cy, idx))

    def _score(cand: tuple[float, float, int]) -> tuple[int, int, int]:
        cx, cy, idx = cand
        hits = _collides(cx, cy + 2.0, half_w, half_h, obstacles)
        on_row = 1 if (b.hi - 5.0 < cy + 2.0 < b.lo + 5.0) else 0
        return (hits, on_row, idx)

    cx, cy, _ = min(candidates, key=_score)
    best_hits = _collides(cx, cy + 2.0, half_w, half_h, obstacles)
    if best_hits > 0 and stats is not None:
        stats.crowded_notes.append(
            {
                "target": f"{n.row.name}@{format_time(n.time, timescale)}",
                "label": n.label,
                "overlaps": best_hits,
            }
        )
    note_labels.append((cx, cy + 2.0, half_w, half_h))

    if isinstance(n.row, Signal) and n.row.kind == "bus" and ay == b.mid:
        before = n.row.value_before(n.time)
        after = n.row.value_at(n.time)
        if not (n.time > 0 and (type(before) is not type(after) or before != after)):
            ay = b.hi if cy < b.mid else b.lo

    cv.line(ax, ay, cx, cy + 2.0, col, width=1.2, opacity=0.85, cap="round")
    cv.add(f'<circle cx="{fmt(ax)}" cy="{fmt(ay)}" r="4.5" fill="{col}" fill-opacity="0.2"/>')
    cv.add(f'<circle cx="{fmt(ax)}" cy="{fmt(ay)}" r="2.2" fill="{col}"/>')
    cv.text(
        cx,
        cy + 3.8,
        n.label,
        col,
        size=10.5,
        anchor="middle",
        weight="500",
        bg=True,
        bg_fill=c["badge_bg"],
        bg_stroke=col,
    )
