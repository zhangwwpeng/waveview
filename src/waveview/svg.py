"""Render a computed DiagramLayout to an SVG string."""

from __future__ import annotations

import math
from fractions import Fraction
from html import escape
from typing import TYPE_CHECKING

from .layout import (
    BADGE_HALF_H,
    BUS_LABEL_HALF_H,
    CAPTION_LINE_H,
    CAPTION_SIZE,
    GAP_W,
    INDENT,
    LABEL_SIZE,
    LANE_H,
    MONO,
    PAD,
    ROW_H,
    SANS,
    TITLE_H,
    VALUE_SIZE,
    Box2D,
    DiagramLayout,
    RenderStats,
    RowBox,
    TimeMap,
    _bezier_path,
    _bezier_pt,
    _clamp_cx,
    _collides,
    _is_dark,
    _near_penalty,
    _nice_step,
    _ribbon_path,
    compute_layout,
    fmt,
    text_width,
    use_metrics,
)
from .model import Arrow, Clock, Note, Row, Signal
from .themes import THEMES
from .units import TIME_UNITS, format_time

if TYPE_CHECKING:
    from .diagram import Diagram


class Canvas:
    def __init__(self, theme: dict[str, str], pfx: str = "", sans: str = SANS, mono: str = MONO):
        self.c = theme
        self.pfx = pfx
        self.sans = sans
        self.mono = mono
        self.parts: list[str] = []
        self.max_right = 0.0  # right edge of the widest badge actually drawn

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
            self.max_right = max(self.max_right, left + w)
            fill = bg_fill or self.c["badge_bg"]
            stroke_attr = (
                f' stroke="{bg_stroke}" stroke-width="0.9" stroke-opacity="0.32"' if bg_stroke else ""
            )
            self.add(
                f'<rect x="{fmt(left)}" y="{fmt(y - size * 0.84)}" width="{fmt(w)}" '
                f'height="{fmt(size * 1.34)}" rx="4" fill="{fill}" fill-opacity="0.92"{stroke_attr}/>'
            )
        # The root <svg> carries the sans stack; only mono text overrides it.
        extra = f' font-family="{self.mono}"' if mono and self.mono != self.sans else ""
        extra += f' text-anchor="{anchor}"' if anchor != "start" else ""
        extra += f' font-weight="{weight}"' if weight else ""
        self.add(f'<text x="{fmt(x)}" y="{fmt(y)}" fill="{color}" font-size="{size}"{extra}>{escape(s)}</text>')


def render(d: Diagram) -> str:
    return render_with_stats(d)[0]


def render_with_stats(d: Diagram) -> tuple[str, RenderStats]:
    with use_metrics(d._fonts.metrics):
        return _render_with_stats(d)


def _render_with_stats(d: Diagram) -> tuple[str, RenderStats]:
    c = THEMES[d.theme]
    is_dark = _is_dark(c)
    lay: DiagramLayout = compute_layout(d)
    stats = lay.stats
    tm = lay.tm
    x0, x_end = lay.x0, lay.x_end
    width, height = lay.width, lay.height
    rows_top, rows_bottom = lay.rows_top, lay.rows_bottom
    axis_y = lay.axis_y
    end = lay.end
    boxes = lay.boxes
    by_row = lay.by_row

    cv = Canvas(c, pfx=f"wv-{d.theme}-", sans=d._fonts.sans, mono=d._fonts.mono)

    # ---- background, title ----
    cv.add(f'<rect width="100%" height="100%" fill="{c["bg"]}"/>')
    if lay.title_y is not None:
        cv.text(PAD, lay.title_y, d.title, c["title"], size=15.5, weight="600")

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
        if float(first_clock.period) * lay.scale >= 10:
            for a, b in tm.visible():
                for t, kind in first_clock.edges_between(a - Fraction(1, 10**18), b):
                    if kind == "rise":
                        x = tm.x(t)
                        cv.line(x, rows_top, x, rows_bottom, c["grid"], width=1)

    if boxes:
        cv.line(x0, rows_top, x0, rows_bottom, c["border"], width=1, opacity=0.65)

    # ---- labels and group headers ----
    for name, depth, top in lay.headers:
        cv.text(PAD + depth * INDENT, top + 17, name, c["group"], size=12, weight="600")
    for depth, top, bottom in lay.brackets:
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
    for a in lay.regions:
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

    # ---- marker label boxes (for region/note collision checks) ----
    marker_labels: list[Box2D] = [
        (cx, (lay.markers_bottom - 8 - lane_idx * 20) - 1.8, text_width(m.label, 10.5) / 2 + 5.5, BADGE_HALF_H)
        for m, cx, lane_idx in lay.marker_items
        if m.label
    ]
    for name, depth, top in lay.headers:
        hw = text_width(name, 12) / 2
        marker_labels.append((PAD + depth * INDENT + hw, top + 13, hw, 7.0))

    # ---- region labels sit cleanly above the waves ----
    region_labels: list[Box2D] = []
    for a in lay.regions:
        if a.label:
            b = by_row[id(a.row)]
            x1, x2 = tm.x(max(a.start, Fraction(0))), tm.x(min(a.end, end))
            hw = text_width(a.label, 10) / 2
            cx = _clamp_cx((x1 + x2) / 2, hw, x0, x_end)
            cy = b.hi - 6.5
            box = (cx, cy - 3.5, hw, 6.5)
            hits = _collides(*box, region_labels + marker_labels)
            if hits > 0:
                stats.crowded_regions.append(
                    {
                        "row": a.row.name,
                        "start": format_time(a.start, d.timescale),
                        "end": format_time(a.end, d.timescale),
                        "label": a.label,
                        "overlaps": hits,
                    }
                )
            cv.text(cx, cy, a.label, c["region_text"], size=10, anchor="middle", weight="600")
            region_labels.append(box)

    # ---- axis line, then contoured S-curve gaps cover waves and axis ----
    cv.line(x0, axis_y, x_end, axis_y, c["border"], width=1.1)
    for g in lay.gaps:
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
    unit_x = x_end + 8
    if lay.scale > 0 and tm.visible():
        unit = TIME_UNITS[d.timescale]
        step = _nice_step(40 / lay.scale / float(unit)) * float(unit)
        step_f = Fraction(str(step))
        step_in_unit = float(step_f / unit)
        tick_digits = max(3, -math.floor(math.log10(step_in_unit)) + 1) if step_in_unit > 0 else 3
        last_tick = math.floor(end / step_f) * step_f
        sample_ticks = [t for t in (last_tick, last_tick - step_f) if t >= 0]
        need_px = (
            max(
                text_width(format_time(t, d.timescale, digits=tick_digits)[: -len(d.timescale)], 10, mono=True)
                for t in sample_ticks
            )
            + 6.0
        )
        if float(step_f) * lay.scale < need_px:
            step = _nice_step(need_px / lay.scale / float(unit)) * float(unit)
            step_f = Fraction(str(step))
            step_in_unit = float(step_f / unit)
            tick_digits = max(3, -math.floor(math.log10(step_in_unit)) + 1) if step_in_unit > 0 else 3
        for a, b in tm.visible():
            k = math.ceil(a / step_f)
            while (t := k * step_f) <= b:
                x = tm.x(t)
                if not any(abs(x - tm.x(g.start)) < 14 or abs(x - tm.x(g.end)) < 14 for g in lay.gaps) or t == a:
                    cv.line(x, axis_y, x, axis_y + 4, c["muted"], width=1)
                    label = format_time(t, d.timescale, digits=tick_digits)[: -len(d.timescale)]
                    cv.text(x, axis_y + 16, label, c["muted"], size=10, anchor="middle", mono=True)
                    # Keep the unit suffix clear of a tick label centred near the right end.
                    unit_x = max(unit_x, x + text_width(label, 10, mono=True) / 2 + 4)
                k += 1
    cv.text(unit_x, axis_y + 4, d.timescale, c["muted"], size=10, mono=True)

    # ---- markers ----
    marker_xs: list[float] = []
    for m, cx, lane_idx in lay.marker_items:
        x = tm.x(m.time)
        marker_xs.append(x)
        ty = lay.markers_bottom - 8 - lane_idx * 20
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
    measure_labels: list[Box2D] = []
    mh_url = cv.url("mh")
    for _m, lane_idx, x1, x2, cx, label, inline in lay.measure_items:
        ly = lay.lanes_top + lane_idx * LANE_H + 16
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
        measure_labels.append((cx, ty - 1.8, text_width(label, 10.5) / 2 + 5.5, BADGE_HALF_H))
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
    min_annot_cy = (PAD + TITLE_H if d.title else PAD) + 5.5
    max_annot_cy = max(min_annot_cy, (lay.lanes_top - 6.0) if lay.measure_items else (axis_y - 10.0))
    arrow_labels: list[Box2D] = []
    for a in lay.arrows:
        _draw_arrow(
            cv,
            a,
            by_row,
            tm,
            x0,
            x_end,
            rows_top - 4.0,
            max_annot_cy,
            region_labels,
            bus_labels,
            marker_labels,
            measure_labels,
            arrow_labels,
            d.timescale,
            stats,
        )

    # ---- single-point callout notes ----
    if lay.notes:
        note_labels: list[Box2D] = []
        min_note_cy = min_annot_cy
        max_note_cy = max_annot_cy
        for n in lay.notes:
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
                measure_labels,
                note_labels,
                d.timescale,
                stats,
            )

    # ---- footer captions ----
    if lay.captions_top is not None:
        cv.line(PAD, lay.captions_top - 6, width - PAD, lay.captions_top - 6, c["border"], width=1, opacity=0.5)
        for idx, line in enumerate(d.captions):
            cv.text(PAD, lay.captions_top + idx * CAPTION_LINE_H + 11, line, c["marker_text"], size=CAPTION_SIZE)

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
    if cv.max_right + PAD > width + 0.05:
        # Safety net: never let a placed badge spill past the viewBox.
        width = cv.max_right + PAD
        stats.width = round(width, 2)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(width)}" height="{fmt(height)}" '
        f'viewBox="0 0 {fmt(width)} {fmt(height)}" font-family="{cv.sans}">' + defs + "".join(cv.parts) + "</svg>\n"
    )
    return svg, stats


def _draw_clock(cv: Canvas, b: RowBox, clk: Clock, tm: TimeMap, d_end: Fraction) -> None:
    color = cv.c["clock"]
    paths = []
    chevrons = []
    show_chevrons = float(min(clk.duty, 1 - clk.duty) * clk.period) * tm.scale >= 12
    for a, end in tm.visible():
        edge_at_a = clk._edge_kind_at(a) if a < d_end else None
        level = clk._level_before(a) if edge_at_a is not None else clk.level_at(a)
        x_start = tm.x(a) - (GAP_W / 2 if a > 0 else 0.0)
        x_stop = tm.x(end) + (GAP_W / 2 if end < d_end else 0.0)
        p = [f"M{fmt(x_start)},{fmt(b.hi if level else b.lo)}"]
        edges = ([(a, edge_at_a)] if edge_at_a is not None else []) + clk.edges_between(a, end)
        for t, kind in edges:
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
        raw_label = sig.display(v)
        if not raw_label.strip():
            continue  # an intentionally blank bus value draws no label
        if not pieces:
            if stats is not None:
                stats.dropped_labels.append(
                    {
                        "signal": sig.name,
                        "start": format_time(t0, timescale),
                        "end": format_time(t1, timescale),
                        "value": raw_label,
                        "reason": "inside_gap",
                    }
                )
            continue
        best = max(pieces, key=lambda p: tm.x(p[1]) - tm.x(p[0]))
        eff_sl = sl if best[0] == t0 else 0.0
        eff_sr = sr if best[1] == t1 else 0.0
        bx1, bx2 = tm.x(best[0]) + eff_sl, tm.x(best[1]) - eff_sr
        room = bx2 - bx1 - 6
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
        bus_labels.append((cx, b.mid + 2.1, text_width(label, VALUE_SIZE, mono=True) / 2, BUS_LABEL_HALF_H))


def _arrow_anchor_y(b: RowBox, row: Row, time: Fraction) -> float:
    if isinstance(row, Clock) and row._edge_kind_at(time) is None:
        return b.hi if row.level_at(time) == 1 else b.lo
    return b.mid


def _draw_arrow(
    cv: Canvas,
    a: Arrow,
    by_row: dict[int, RowBox],
    tm: TimeMap,
    x0: float,
    x_end: float,
    min_cy: float,
    max_cy: float,
    region_labels: list[Box2D],
    bus_labels: list[Box2D],
    marker_labels: list[Box2D],
    measure_labels: list[Box2D],
    arrow_labels: list[Box2D],
    timescale: str = "ns",
    stats: RenderStats | None = None,
) -> None:
    c = cv.c
    b1, b2 = by_row[id(a.src.signal)], by_row[id(a.dst.signal)]
    x1 = tm.x(a.src.time)
    y1 = _arrow_anchor_y(b1, a.src.signal, a.src.time)
    x2 = tm.x(a.dst.time)
    y2 = _arrow_anchor_y(b2, a.dst.signal, a.dst.time)
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
        half_h = BADGE_HALF_H
        obstacles = region_labels + bus_labels + marker_labels + measure_labels + arrow_labels

        def _arrow_score(item: tuple[int, float, float, float]) -> tuple[int, float, int]:
            idx, px, py, disp = item
            return (
                _collides(px, py + 2.0, half_w, half_h, obstacles),
                round(_near_penalty(px, py + 2.0, half_w, half_h, obstacles) + disp, 2),
                idx,
            )

        if same_row:
            candidates = []
            for test_lift in (22.0, 32.0, 42.0):
                tc1, tc2 = (x1, y1 - test_lift), (x2, y2 - test_lift)
                for t in (0.5, 0.35, 0.65):
                    px, py = _bezier_pt(x1, y1, tc1, tc2, x2, y2, t)
                    px = _clamp_cx(px, half_w, x0, x_end)
                    disp = (test_lift - 22.0) * 0.25 + (0.0 if t == 0.5 else 2.5)
                    candidates.append((px, py, disp, tc1, tc2))
            best_idx = min(
                range(len(candidates)),
                key=lambda i: _arrow_score((i, candidates[i][0], candidates[i][1], candidates[i][2])),
            )
            mx, my, _, c1, c2 = candidates[best_idx]
        else:
            candidates_diff: list[tuple[float, float, float]] = []
            for t in (0.5, 0.35, 0.65, 0.25, 0.75):
                px, py = _bezier_pt(x1, y1, c1, c2, x2, y2, t)
                candidates_diff.append((_clamp_cx(px, half_w, x0, x_end), py, abs(t - 0.5) * 10.0))
            base_x, base_y = _bezier_pt(x1, y1, c1, c2, x2, y2, 0.5)
            base_x = _clamp_cx(base_x, half_w, x0, x_end)
            lo_cy = min(min_cy, max_cy)
            for dy in (-14.0, 14.0, -24.0, 24.0):
                clamped_y = min(max(base_y + dy, lo_cy), max_cy)
                candidates_diff.append((base_x, clamped_y, 6.0 if abs(dy) <= 14.0 else 10.0))
            best_idx = min(
                range(len(candidates_diff)),
                key=lambda i: _arrow_score((i, *candidates_diff[i])),
            )
            mx, my, _ = candidates_diff[best_idx]

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


_NOTE_DISP = (0.0, 2.5, 2.5, 5.0, 5.0, 5.0, 1.5, 4.0, 4.0, 6.5, 6.5, 6.5)


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
    measure_labels: list[Box2D],
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
    half_h = BADGE_HALF_H
    obstacles = region_labels + bus_labels + arrow_labels + marker_labels + measure_labels + note_labels

    above = [(0.0, -21.0), (22.0, -21.0), (-22.0, -21.0), (32.0, -30.0), (-32.0, -30.0), (0.0, -32.0)]
    below = [(0.0, 19.0), (22.0, 19.0), (-22.0, 19.0), (32.0, 28.0), (-32.0, 28.0), (0.0, 30.0)]
    offsets = below + above if ay > b.mid else above + below

    candidates: list[tuple[float, float, int]] = []
    for idx, (dx, dy) in enumerate(offsets):
        cx = _clamp_cx(ax + dx, half_w, x0, x_end)
        cy = min(max(b.mid + dy, min_cy), max_cy)
        candidates.append((cx, cy, idx))

    def _score(cand: tuple[float, float, int]) -> tuple[int, int, float, int]:
        cx, cy, idx = cand
        hits = _collides(cx, cy + 2.0, half_w, half_h, obstacles)
        on_row = 1 if (b.hi - 5.0 < cy + 2.0 < b.lo + 5.0) else 0
        near = _near_penalty(cx, cy + 2.0, half_w, half_h, obstacles)
        return (hits, on_row, round(near + _NOTE_DISP[idx], 2), idx)

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
