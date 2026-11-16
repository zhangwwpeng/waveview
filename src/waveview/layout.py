"""Geometric layout computation, time-to-pixel mapping, and collision metrics."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from .fonts import DEFAULT_MONO, DEFAULT_SANS
from .model import Arrow, Clock, Gap, Marker, Measure, Note, Region, Row, Signal, Value
from .units import format_time

if TYPE_CHECKING:
    from .diagram import Diagram

SANS = DEFAULT_SANS
MONO = DEFAULT_MONO

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
BUS_LABEL_HALF_H = 7.5
BADGE_HALF_H = 7.1
NEAR_PAD_X = 10.0
NEAR_PAD_Y = round((ROW_H - 2 * BUS_LABEL_HALF_H - 2 * BADGE_HALF_H) / 2 - 0.6, 2)

_WIDE_SYMBOLS = frozenset("←→↑↓↔⇒⇐…—–")

Box2D = tuple[float, float, float, float]  # cx, cy, half_w, half_h


def text_width(s: str, size: float, mono: bool = False) -> float:
    w = 0.0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            w += size * 1.04
        elif ch in _WIDE_SYMBOLS:
            w += size * 0.95
        elif mono:
            w += size * 0.61
        elif ch == " ":
            w += size * 0.34
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
    target_width: float | None = None
    hit_width_cap: bool = False
    width_drivers: list[dict[str, Any]] = field(default_factory=list)
    truncated_labels: list[dict[str, Any]] = field(default_factory=list)
    dropped_labels: list[dict[str, Any]] = field(default_factory=list)
    crowded_arrows: list[dict[str, Any]] = field(default_factory=list)
    crowded_notes: list[dict[str, Any]] = field(default_factory=list)
    crowded_regions: list[dict[str, Any]] = field(default_factory=list)


def _label_rail_x0(d: Diagram) -> float:
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
    return PAD + label_w + 22


def _resolve_scale(
    d: Diagram,
    visible_span: Fraction,
    gaps: list[Gap],
    end: Fraction,
    x0: float,
) -> tuple[float, bool, list[dict[str, Any]]]:
    if d.px_per_ns:
        return d.px_per_ns * 1e9, False, []

    clocks = [r for r in d.rows if isinstance(r, Clock)]
    span = float(visible_span) or 1e-9
    wave_base = x0 + len(gaps) * GAP_W + PAD + 26
    if not clocks:
        default_scale = 900 / span
        floor = (40.0 / float(visible_span)) if (d.width is not None and visible_span > 0) else 0.0
    else:
        half = float(min(min(c.duty, 1 - c.duty) * c.period for c in clocks))
        default_scale, floor = 16 / half, 5 / half

    # Widen until every bus value fits in its own visible sub-segment.
    bus_max_scale = 0.0
    bus_peaks: list[dict[str, Any]] = []
    for r in d.rows:
        if isinstance(r, Signal) and r.kind == "bus":
            best_need = 0.0
            best_seg: tuple[Fraction, Fraction, Value] | None = None
            for t0, t1, v in r.segments(end):
                if v in ("x", "z"):
                    continue
                vis = _max_visible_span(t0, t1, gaps)
                if vis <= 0:
                    continue
                need_scale = (text_width(r.display(v), VALUE_SIZE, mono=True) + 18) / float(vis)
                if need_scale > best_need:
                    best_need = need_scale
                    best_seg = (t0, t1, v)
            if best_seg is not None:
                bus_max_scale = max(bus_max_scale, best_need)
                t0, t1, v = best_seg
                bus_peaks.append(
                    {
                        "kind": "bus",
                        "signal": r.name,
                        "value": r.display(v),
                        "start": format_time(t0, d.timescale),
                        "end": format_time(t1, d.timescale),
                        "min_width": round(wave_base + float(visible_span) * best_need, 2),
                        "need_px_per_ns": round(best_need / 1e9, 4),
                        "_scale": best_need,
                    }
                )

    if d.width is not None:
        avail_wave_px = d.width - wave_base
        target_scale = (avail_wave_px / float(visible_span)) if (visible_span > 0 and avail_wave_px > 0) else 0.0
        scale = max(target_scale, floor, bus_max_scale, 1e9 if visible_span <= 0 else 0.0)
        drivers: list[dict[str, Any]] = []
        floor_width = wave_base + float(visible_span) * floor
        if floor_width > d.width + 0.05:
            if clocks:
                drivers.append(
                    {
                        "kind": "clock_floor",
                        "min_width": round(floor_width, 2),
                        "need_px_per_ns": round(floor / 1e9, 4),
                    }
                )
            else:
                drivers.append({"kind": "label_rail", "min_width": round(floor_width, 2)})
        for item in bus_peaks:
            if item["min_width"] > d.width + 0.05:
                drivers.append({k: val for k, val in item.items() if k != "_scale"})
        return scale, False, drivers

    scale = max(default_scale, bus_max_scale)
    hit_cap = False
    drivers = []
    if d.max_width is not None and wave_base + float(visible_span) * scale > d.max_width:
        cap_scale = max(1.0, d.max_width - wave_base) / span
        if floor > cap_scale:
            drivers.append(
                {
                    "kind": "clock_floor",
                    "min_width": round(wave_base + float(visible_span) * floor, 2),
                    "need_px_per_ns": round(floor / 1e9, 4),
                }
            )
        for item in bus_peaks:
            if item["_scale"] > cap_scale:
                drivers.append({k: val for k, val in item.items() if k != "_scale"})
        drivers.sort(key=lambda item: item["min_width"], reverse=True)
        scale = max(cap_scale, floor)
        hit_cap = True
    if visible_span <= 0:
        scale = max(scale, 1e9)
    return scale, hit_cap, drivers


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


def _clamp_cx(cx: float, half_w: float, x0: float, x_end: float) -> float:
    if x_end - x0 >= half_w * 2 + 4:
        return min(max(cx, x0 + half_w + 2), x_end - half_w - 2)
    return max(PAD + half_w, cx)


def _wave_segments(
    cx: float, top: float, bottom: float, amp: float = 2.5
) -> list[tuple[float, float, float, float, float, float]]:
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
        parts.append(
            f"C{fmt(cx2 + d)},{fmt(yb - dy * 0.35)} {fmt(cx2 + d)},{fmt(ya + dy * 0.35)} {fmt(cx2)},{fmt(ya)}"
        )
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


def _collides(cx: float, cy: float, hw: float, hh: float, obstacles: list[Box2D]) -> int:
    hits = 0
    for ox, oy, ohw, ohh in obstacles:
        if abs(cx - ox) < hw + ohw + 4 and abs(cy - oy) < hh + ohh + 3:
            hits += 1
    return hits


def _near_penalty(
    cx: float,
    cy: float,
    hw: float,
    hh: float,
    obstacles: list[Box2D],
    pad_x: float = NEAR_PAD_X,
    pad_y: float = NEAR_PAD_Y,
) -> float:
    """Soft penalty for boxes that clear hard collision (4x3px) yet graze within pad_x x pad_y (<5.3px inter-row corridor)."""
    penalty = 0.0
    for ox, oy, ohw, ohh in obstacles:
        gx = abs(cx - ox) - (hw + ohw)
        gy = abs(cy - oy) - (hh + ohh)
        if gx < pad_x and gy < pad_y:
            penalty += (pad_x - gx) * (pad_y - gy)
    return round(penalty, 2)


@dataclass
class DiagramLayout:
    end: Fraction
    gaps: list[Gap]
    scale: float
    tm: TimeMap
    stats: RenderStats
    x0: float
    x_end: float
    width: float
    height: float
    title_y: float | None
    markers_bottom: float
    rows_top: float
    rows_bottom: float
    lanes_top: float
    axis_y: float
    captions_top: float | None
    boxes: list[RowBox]
    by_row: dict[int, RowBox]
    headers: list[tuple[str, int, float]]
    brackets: list[tuple[int, float, float]]
    marker_items: list[tuple[Marker, float, int]]
    measure_items: list[tuple[Measure, int, float, float, float, str, bool]]
    arrows: list[Arrow]
    regions: list[Region]
    notes: list[Note]


def compute_layout(d: Diagram) -> DiagramLayout:
    """Compute all coordinates, lane packing, and canvas bounds for ``d``."""
    end = d.end
    gaps = _merge_gaps(d.gaps, end)
    visible_span = end - sum((g.end - g.start for g in gaps), Fraction(0))
    x0 = _label_rail_x0(d)
    scale, hit_cap, wave_drivers = _resolve_scale(d, visible_span, gaps, end, x0)
    stats = RenderStats(
        px_per_ns=scale / 1e9,
        target_width=d.width,
        hit_width_cap=hit_cap,
        width_drivers=wave_drivers,
    )

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

    tm = TimeMap(x0, scale, gaps, end)
    x_end = tm.x(end)

    marker_items: list[tuple[Marker, float, int]] = []
    marker_lane_rights: list[float] = []
    marker_max_right = 0.0
    widest_marker: tuple[Marker, float] | None = None
    for m in sorted(markers, key=lambda mk: tm.x(mk.time)):
        x = tm.x(m.time)
        if not m.label:
            marker_items.append((m, x, 0))
            continue
        half_w = text_width(m.label, 10.5) / 2 + 5.5
        cx = max(x, PAD + half_w)
        lane_idx = 0
        while lane_idx < len(marker_lane_rights):
            if marker_lane_rights[lane_idx] + 6 <= cx - half_w:
                break
            lane_idx += 1
        if lane_idx == len(marker_lane_rights):
            marker_lane_rights.append(cx + half_w)
        else:
            marker_lane_rights[lane_idx] = cx + half_w
        if cx + half_w > marker_max_right:
            marker_max_right = cx + half_w
            widest_marker = (m, cx + half_w)
        marker_items.append((m, cx, lane_idx))
    marker_band_h = (MARKER_BAND + max(0, len(marker_lane_rights) - 1) * 20) if marker_lane_rights else 0

    measure_lanes: list[list[tuple[float, float]]] = []
    measure_items: list[tuple[Measure, int, float, float, float, str, bool]] = []
    measure_right = 0.0
    for m in measures:
        x1 = tm.x(max(m.start, Fraction(0)))
        x2 = tm.x(min(m.end, end))
        label = m.label or format_time(m.end - m.start, d.timescale)
        half_w = text_width(label, 10.5) / 2 + 5.5
        inline = (x2 - x1) >= text_width(label, 10.5) + 32
        cx = _clamp_cx((x1 + x2) / 2, half_w, x0, x_end)
        occ_l = min(x1, cx - half_w)
        occ_r = max(x2, cx + half_w)
        measure_right = max(measure_right, occ_r + PAD)
        lane_idx = 0
        while lane_idx < len(measure_lanes):
            if all(occ_r + 8 <= la or occ_l - 8 >= lb for la, lb in measure_lanes[lane_idx]):
                break
            lane_idx += 1
        if lane_idx == len(measure_lanes):
            measure_lanes.append([])
        measure_lanes[lane_idx].append((occ_l, occ_r))
        measure_items.append((m, lane_idx, x1, x2, cx, label, inline))

    y = float(PAD)
    title_y = None
    if d.title:
        title_y = y + 19
        y += TITLE_H
    y += marker_band_h
    markers_bottom = y
    if d.rows and any(a.src.signal is a.dst.signal and a.src.signal is d.rows[0] for a in arrows):
        y += 14
    if d.rows and not d.rows[0].groups and any(n.row is d.rows[0] for n in notes):
        y += 18
    rows_top = y

    boxes: list[RowBox] = []
    headers: list[tuple[str, int, float]] = []
    brackets: list[tuple[int, float, float]] = []
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
    widest_caption = ""
    if d.captions:
        captions_top = axis_y + AXIS_H + CAPTION_TOP_GAP
        height = captions_top + len(d.captions) * CAPTION_LINE_H + PAD - 4
        for line in d.captions:
            right = PAD + text_width(line, CAPTION_SIZE) + PAD
            if right > caption_right:
                caption_right = right
                widest_caption = line

    note_min_right = 0.0
    widest_note: Note | None = None
    for n in notes:
        right = x0 + text_width(n.label, 10.5) + 16 + PAD
        if right > note_min_right:
            note_min_right = right
            widest_note = n
    region_right = max(
        (
            _clamp_cx(
                (tm.x(max(a.start, Fraction(0))) + tm.x(min(a.end, end))) / 2,
                text_width(a.label, 10) / 2,
                x0,
                x_end,
            )
            + text_width(a.label, 10) / 2
            + PAD
            for a in regions
            if a.label
        ),
        default=0.0,
    )
    arrow_right = max(
        (
            _clamp_cx(
                (tm.x(a.src.time) + tm.x(a.dst.time)) / 2
                + 0.35 * abs(tm.x(a.dst.time) - tm.x(a.src.time)),
                text_width(a.label, 10.5) / 2 + 5.5,
                x0,
                x_end,
            )
            + text_width(a.label, 10.5) / 2
            + 5.5
            + PAD
            for a in arrows
            if a.label
        ),
        default=0.0,
    )
    title_right = (PAD + text_width(d.title, 15.5) + PAD) if d.title else 0.0
    marker_right = (marker_max_right + PAD) if marker_max_right else 0.0
    width = max(
        x_end + PAD + 26,
        title_right,
        marker_right,
        measure_right,
        region_right,
        arrow_right,
        caption_right,
        note_min_right,
    )
    stats.width = round(width, 2)
    stats.height = round(height, 2)
    if d.width is not None:
        if title_right > d.width + 0.05 and d.title:
            stats.width_drivers.append({"kind": "title", "value": d.title, "min_width": round(title_right, 2)})
        if caption_right > d.width + 0.05 and widest_caption:
            stats.width_drivers.append(
                {"kind": "caption", "value": widest_caption, "min_width": round(caption_right, 2)}
            )
        if note_min_right > d.width + 0.05 and widest_note is not None:
            stats.width_drivers.append(
                {
                    "kind": "note",
                    "row": widest_note.row.name,
                    "value": widest_note.label,
                    "min_width": round(note_min_right, 2),
                }
            )
        if marker_right > max(d.width, x_end + PAD + 26) + 0.05 and widest_marker is not None:
            stats.width_drivers.append(
                {
                    "kind": "marker",
                    "value": widest_marker[0].label or "",
                    "min_width": round(marker_right, 2),
                }
            )
        if stats.width <= d.width + 0.05:
            stats.width_drivers.clear()
        else:
            stats.width_drivers.sort(key=lambda item: item["min_width"], reverse=True)

    return DiagramLayout(
        end=end,
        gaps=gaps,
        scale=scale,
        tm=tm,
        stats=stats,
        x0=x0,
        x_end=x_end,
        width=width,
        height=height,
        title_y=title_y,
        markers_bottom=markers_bottom,
        rows_top=rows_top,
        rows_bottom=rows_bottom,
        lanes_top=lanes_top,
        axis_y=axis_y,
        captions_top=captions_top,
        boxes=boxes,
        by_row={id(b.row): b for b in boxes},
        headers=headers,
        brackets=brackets,
        marker_items=marker_items,
        measure_items=measure_items,
        arrows=arrows,
        regions=regions,
        notes=notes,
    )
