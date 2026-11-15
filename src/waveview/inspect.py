"""Structured timing inspection and layout self-check for AI & scripts."""

from __future__ import annotations

import json
import warnings
import xml.etree.ElementTree as ET
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Literal

from .errors import WaveError, WaveWarning
from .model import Arrow, Clock, EdgeKind, Marker, Measure, Note, Region, Signal
from .svg import TimeMap, _merge_gaps, render_with_stats
from .units import format_freq, format_time

if TYPE_CHECKING:
    from .diagram import Diagram


def _edge_index(clk: Clock, kind: EdgeKind, t: Fraction) -> int:
    if not clk._gated:
        base = clk._base(kind)
        k = int((t - base) / clk.period)
        return k - clk._first_k(kind) + 1
    return clk._count_gated_edges(kind, t)


def sample_clock(
    d: Diagram,
    clk: Clock | str,
    edge: EdgeKind = "rise",
    *,
    include_gaps: bool = False,
) -> list[dict[str, Any]]:
    """Sample all signals at every active ``edge`` of ``clk`` within ``[0, d.end)``.

    For each edge timestamp ``t``:
    - ``sampled`` is the signal value strictly before ``t`` (what a flip-flop captures).
    - ``after`` is the signal value at ``t`` (after synchronous updates at ``t``).
    """
    clock = d._clock(clk)
    if edge not in ("rise", "fall"):
        raise WaveError(f'edge 只能是 "rise" 或 "fall"，收到的是 {edge!r}')

    end = d.end
    gaps = _merge_gaps(d.gaps, end)
    intervals = [(Fraction(0), end)] if include_gaps else TimeMap(0.0, 1.0, gaps, end).visible()
    signals = [r for r in d.rows if isinstance(r, Signal)]

    samples: list[dict[str, Any]] = []
    seen_times: set[Fraction] = set()
    for a, b in intervals:
        if a >= end:
            continue
        b_clamped = min(b, end)
        edge_times: list[Fraction] = []
        if clock._edge_kind_at(a) == edge and a < end:
            edge_times.append(a)
        for t, kind in clock.edges_between(a, b_clamped):
            if kind == edge and t < end:
                edge_times.append(t)

        for t in edge_times:
            if t in seen_times:
                continue
            seen_times.add(t)
            sig_map: dict[str, dict[str, Any]] = {}
            for sig in signals:
                before = sig.value_before(t)
                after = sig.value_at(t)
                changed = type(before) is not type(after) or before != after
                sig_map[sig.name] = {
                    "sampled": sig.display(before),
                    "after": sig.display(after),
                    "changed": changed,
                }
            samples.append(
                {
                    "edge": _edge_index(clock, edge, t),
                    "time": format_time(t, d.timescale),
                    "time_s": float(t),
                    "signals": sig_map,
                }
            )
    return samples


def to_dict(
    d: Diagram,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Return full timing semantics, rendered SVG metrics, warnings, and layout diagnostics."""
    ts = d.timescale
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", WaveWarning)
        d.check()
    all_warnings = list(extra_warnings or []) + [str(w.message) for w in caught]

    svg, stats = render_with_stats(d)
    svg_valid = True
    svg_error: str | None = None
    try:
        ET.fromstring(svg)
    except ET.ParseError as exc:
        svg_valid = False
        svg_error = str(exc)

    layout_issues: list[str] = []
    for item in stats.truncated_labels:
        layout_issues.append(
            f'总线截断：{item["signal"]}@{item["start"]}~{item["end"]} 值 "{item["value"]}" 被截断为 "{item["shown"]}"'
        )
    for item in stats.dropped_labels:
        layout_issues.append(
            f'总线丢字：{item["signal"]}@{item["start"]}~{item["end"]} 值 "{item["value"]}" 因分段过窄未能显示'
        )
    for item in stats.crowded_arrows:
        layout_issues.append(
            f'箭头拥挤：{item["src"]} → {item["dst"]} "{item["label"]}" 与其他文字存在 {item["overlaps"]} 处重叠'
        )
    for item in stats.crowded_notes:
        layout_issues.append(
            f'注释拥挤：{item["target"]} "{item["label"]}" 与其他文字存在 {item["overlaps"]} 处重叠'
        )
    if stats.hit_width_cap and (stats.truncated_labels or stats.dropped_labels):
        layout_issues.append(
            "画布已达 2400px 自动宽度上限；可显式传 Diagram(px_per_ns=...) 或用 d.gap(...) 折叠长空闲"
        )

    clocks = [r for r in d.rows if isinstance(r, Clock)]
    signals = [r for r in d.rows if isinstance(r, Signal)]
    end = d.end

    clock_data = [
        {
            "name": c.name,
            "label": c.label,
            "freq": format_freq(c.freq),
            "period": format_time(c.period, ts),
            "phase": format_time(c.phase, ts),
            "duty": float(c.duty),
            "gated": c._gated,
            "idle": c.idle if c._gated else None,
            "windows": [
                [format_time(ws, ts), format_time(we, ts) if we is not None else None]
                for ws, we in c.windows
            ],
            "groups": list(c.groups),
        }
        for c in clocks
    ]

    signal_data = [
        {
            "name": s.name,
            "label": s.label,
            "kind": s.kind,
            "width": s.width,
            "fmt": s.fmt if s.kind == "bus" else None,
            "init": s.display(s.init),
            "groups": list(s.groups),
            "segments": [
                {
                    "start": format_time(t0, ts),
                    "end": format_time(t1, ts),
                    "value": s.display(v),
                }
                for t0, t1, v in s.segments(end)
            ],
        }
        for s in signals
    ]

    step_data = [
        {
            "time": format_time(step.time, ts),
            "cause": step.cause,
            "changes": [
                {
                    "signal": sig.name,
                    "old": sig.display(sig.value_before(step.time)),
                    "new": sig.display(new),
                }
                for sig, _old, new in step.changes
            ],
        }
        for step in sorted(d.steps, key=lambda s: s.time)
    ]

    ann_data: list[dict[str, Any]] = []
    for a in d.annotations:
        if isinstance(a, Arrow):
            ann_data.append(
                {
                    "type": "arrow",
                    "src": f"{a.src.signal.name}@{format_time(a.src.time, ts)}",
                    "dst": f"{a.dst.signal.name}@{format_time(a.dst.time, ts)}",
                    "label": a.label,
                }
            )
        elif isinstance(a, Region):
            ann_data.append(
                {
                    "type": "region",
                    "row": a.row.name,
                    "start": format_time(a.start, ts),
                    "end": format_time(a.end, ts),
                    "label": a.label,
                }
            )
        elif isinstance(a, Marker):
            ann_data.append(
                {
                    "type": "marker",
                    "time": format_time(a.time, ts),
                    "label": a.label,
                }
            )
        elif isinstance(a, Measure):
            ann_data.append(
                {
                    "type": "measure",
                    "start": format_time(a.start, ts),
                    "end": format_time(a.end, ts),
                    "duration": format_time(a.end - a.start, ts),
                    "label": a.label,
                }
            )
        elif isinstance(a, Note):
            ann_data.append(
                {
                    "type": "note",
                    "target": f"{a.row.name}@{format_time(a.time, ts)}",
                    "row": a.row.name,
                    "time": format_time(a.time, ts),
                    "label": a.label,
                }
            )

    gaps_merged = _merge_gaps(d.gaps, end)
    ok = svg_valid and not all_warnings and not layout_issues

    return {
        "ok": ok,
        "svg_valid": svg_valid,
        "svg_error": svg_error,
        "title": d.title,
        "timescale": ts,
        "theme": d.theme,
        "end": format_time(end, ts),
        "canvas": {
            "width": stats.width,
            "height": stats.height,
            "px_per_ns": round(stats.px_per_ns, 4),
            "hit_width_cap": stats.hit_width_cap,
            "truncated_labels": stats.truncated_labels,
            "dropped_labels": stats.dropped_labels,
            "crowded_arrows": stats.crowded_arrows,
            "crowded_notes": stats.crowded_notes,
        },
        "warnings": all_warnings,
        "layout_issues": layout_issues,
        "clocks": clock_data,
        "signals": signal_data,
        "steps": step_data,
        "clock_samples": {c.name: sample_clock(d, c, "rise") for c in clocks},
        "gaps": [{"start": format_time(g.start, ts), "end": format_time(g.end, ts)} for g in gaps_merged],
        "annotations": ann_data,
        "captions": list(d.captions),
    }


def inspect_diagram(
    d: Diagram,
    fmt: Literal["text", "json"] = "text",
    extra_warnings: list[str] | None = None,
) -> str:
    """Format diagram inspection & self-check report as ``"text"`` or ``"json"``."""
    if fmt not in ("text", "json"):
        raise WaveError(f'inspect 格式只能是 "text" 或 "json"，收到的是 {fmt!r}')

    info = to_dict(d, extra_warnings=extra_warnings)
    if fmt == "json":
        return json.dumps(info, ensure_ascii=False, indent=2)

    status = "OK" if info["ok"] else ("ERROR" if not info["svg_valid"] else "WARN")
    canvas = info["canvas"]
    lines = [
        f"[waveview check] {status} | {canvas['width']:g}x{canvas['height']:g}px | "
        f"theme={info['theme']} | end={info['end']} | svg={'valid' if info['svg_valid'] else 'INVALID'}"
    ]
    if info["svg_error"]:
        lines.append(f"SVG 解析失败：{info['svg_error']}")
    if info["warnings"]:
        lines.append(f"告警（{len(info['warnings'])}）：")
        for msg in info["warnings"]:
            lines.append(f"  - {msg}")
    if info["layout_issues"]:
        lines.append(f"排版诊断（{len(info['layout_issues'])}）：")
        for msg in info["layout_issues"]:
            lines.append(f"  - {msg}")

    lines.append("")
    lines.append("=== 时序脚本摘要 (describe) ===")
    lines.append(d.describe())

    samples_by_clock: dict[str, list[dict[str, Any]]] = info["clock_samples"]
    if samples_by_clock and info["signals"]:
        lines.append("")
        lines.append("=== 时钟上升沿采样 (@posedge) ===")
        for clk_name, rows in samples_by_clock.items():
            if not rows:
                lines.append(f"时钟 {clk_name}：可见范围内无激活上升沿")
                continue
            lines.append(f"时钟 {clk_name}（共 {len(rows)} 拍）：")
            for row in rows:
                parts = []
                for sig_name, st in row["signals"].items():
                    val = f"{st['sampled']}→{st['after']}" if st["changed"] else st["sampled"]
                    parts.append(f"{sig_name}={val}")
                lines.append(f"  #{row['edge']:<3} t={row['time']:<9} | {' '.join(parts)}")

    return "\n".join(lines)
