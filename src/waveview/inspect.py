"""Structured timing inspection and layout self-check for AI & scripts."""

from __future__ import annotations

import json
import warnings
import xml.etree.ElementTree as ET
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Literal

from .diagram import Event, Wait
from .errors import WaveError, WaveWarning
from .layout import TimeMap, _merge_gaps
from .model import Arrow, Clock, EdgeKind, Marker, Measure, Note, Region, Signal, Value
from .svg import render_with_stats
from .units import format_freq, format_time

if TYPE_CHECKING:
    from .diagram import Diagram, TimeRef


def _format_width_driver(drv: dict[str, Any]) -> str:
    kind = drv["kind"]
    min_w = drv.get("min_width", 0)
    if kind == "bus":
        return f'{drv["signal"]}="{drv["value"]}" (需 ≥{min_w:g}px)'
    if kind == "clock_floor":
        return f"时钟最小周期宽度 (需 ≥{min_w:g}px)"
    if kind == "label_rail":
        return f"左侧标签栏与边距 (需 ≥{min_w:g}px)"
    if kind == "caption":
        return f"图注过长 (需 ≥{min_w:g}px)"
    if kind == "title":
        return f"标题过长 (需 ≥{min_w:g}px)"
    if kind == "note":
        return f'注释过长 {drv["row"]}="{drv["value"]}" (需 ≥{min_w:g}px)'
    if kind == "marker":
        return f'标记过长 "{drv["value"]}" (需 ≥{min_w:g}px)'
    return f"{kind} (需 ≥{min_w:g}px)"


def _edge_index(clk: Clock, kind: EdgeKind, t: Fraction) -> int:
    if not clk._gated:
        base = clk._base(kind)
        k = int((t - base) / clk.period)
        return k - clk._first_k(kind) + 1
    return clk._count_gated_edges(kind, t)


def _value_after_edge(d: Diagram, clock: Clock, t: Fraction, sig: Signal) -> Value:
    """Return ``sig``'s value after synchronous updates driven by ``clock`` at ``t``."""
    after = sig.value_at(t)
    matching = [s for s in d.steps if s.edge_clock is clock and s.edge_time == t]
    for step in sorted(matching, key=lambda s: s.time):
        for s, _old, new in step.changes:
            if s is sig:
                after = new
    return after


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
                after = _value_after_edge(d, clock, t, sig)
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


def find_handshakes(
    d: Diagram,
    clk: Clock | str,
    valid: Signal | str,
    ready: Signal | str,
    *,
    edge: EdgeKind = "rise",
    active: Value = 1,
    include_gaps: bool = False,
) -> list[dict[str, Any]]:
    """Return clock edges where both ``valid`` and ``ready`` sample equal to ``active``."""
    clock = d._clock(clk)
    v_sig = d._signal_ref(valid)
    r_sig = d._signal_ref(ready)
    v_target = v_sig.display(d._normalise(v_sig, active))
    r_target = r_sig.display(d._normalise(r_sig, active))
    rows = sample_clock(d, clock, edge=edge, include_gaps=include_gaps)
    return [
        {"edge": row["edge"], "time": row["time"], "time_s": row["time_s"]}
        for row in rows
        if row["signals"][v_sig.name]["sampled"] == v_target
        and row["signals"][r_sig.name]["sampled"] == r_target
    ]


def expect_handshake(
    d: Diagram,
    clk: Clock | str,
    valid: Signal | str,
    ready: Signal | str,
    *,
    at: TimeRef | None = None,
    count: int | None = None,
    edge: EdgeKind = "rise",
    active: Value = 1,
    include_gaps: bool = False,
) -> list[dict[str, Any]]:
    """Assert valid/ready handshake at ``at`` and/or exact ``count``, raising ``WaveError`` on mismatch."""
    clock = d._clock(clk)
    v_sig = d._signal_ref(valid)
    r_sig = d._signal_ref(ready)
    if edge not in ("rise", "fall"):
        raise WaveError(f'edge 只能是 "rise" 或 "fall"，收到的是 {edge!r}')
    v_val = d._normalise(v_sig, active)
    r_val = d._normalise(r_sig, active)
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        raise WaveError(f"count 必须是非负整数，收到的是 {count!r}")

    actual = find_handshakes(d, clock, v_sig, r_sig, edge=edge, active=active, include_gaps=include_gaps)

    if at is not None:
        if (
            isinstance(at, (Event, Wait))
            and at._edge_clock is clock
            and at._edge_time is not None
            and clock._edge_kind_at(at._edge_time) == edge
        ):
            edge_t = at._edge_time
        else:
            raw_t = d._resolve(at)
            if raw_t < 0:
                raise WaveError(f"expect_handshake 的 at 时间不能是负数，收到的是 {at!r}")
            if clock._edge_kind_at(raw_t) == edge:
                edge_t = raw_t
            elif clock.tco > 0 and raw_t - clock.tco >= 0 and clock._edge_kind_at(raw_t - clock.tco) == edge:
                edge_t = raw_t - clock.tco
            else:
                label = "上升" if edge == "rise" else "下降"
                raise WaveError(f'{format_time(raw_t, d.timescale)} 不是时钟 "{clock.name}" 的有效{label}沿')

        if not include_gaps:
            end = d.end
            visible = TimeMap(0.0, 1.0, _merge_gaps(d.gaps, end), end).visible()
            if edge_t < end and not any(a <= edge_t <= b for a, b in visible):
                raise WaveError(
                    f'{format_time(edge_t, d.timescale)} 位于 gap 省略区间内（如确实要断言该拍，请传 include_gaps=True）'
                )

        v_before = v_sig.value_before(edge_t)
        r_before = r_sig.value_before(edge_t)
        v_ok = type(v_before) is type(v_val) and v_before == v_val
        r_ok = type(r_before) is type(r_val) and r_before == r_val
        if not (v_ok and r_ok):
            idx = _edge_index(clock, edge, edge_t)
            sym = "↑" if edge == "rise" else "↓"
            t_str = format_time(edge_t, d.timescale)
            v_after = _value_after_edge(d, clock, edge_t, v_sig)
            r_after = _value_after_edge(d, clock, edge_t, r_sig)
            hints: list[str] = []
            if not v_ok and type(v_after) is type(v_val) and v_after == v_val:
                hints.append(f'"{v_sig.name}" 在该沿才刚变为 {v_sig.display(v_after)}，要到下一拍才会被采样')
            if not r_ok and type(r_after) is type(r_val) and r_after == r_val:
                hints.append(f'"{r_sig.name}" 在该沿才刚变为 {r_sig.display(r_after)}，要到下一拍才会被采样')
            hint_part = f"（{'；'.join(hints)}）" if hints else ""
            if actual:
                beats = ", ".join(f'#{h["edge"]}({h["time"]})' for h in actual)
                actual_part = f"实际发生握手的边沿：{beats}"
            else:
                actual_part = f'整张图中 "{v_sig.name}" 与 "{r_sig.name}" 从未同拍完成握手'
            raise WaveError(
                f"在 {clock.name}{sym}#{idx} ({t_str}) 未发生握手："
                f"采样到 {v_sig.name}={v_sig.display(v_before)}, {r_sig.name}={r_sig.display(r_before)}"
                f"{hint_part}；{actual_part}"
            )

    if count is not None:
        if len(actual) != count:
            beats = ", ".join(f'#{h["edge"]}({h["time"]})' for h in actual) if actual else "无"
            raise WaveError(
                f'期望 "{v_sig.name}"/"{r_sig.name}" 在 {clock.name} 上发生 {count} 次握手，'
                f"实际发生 {len(actual)} 次（{beats}）"
            )
    elif at is None and not actual:
        raise WaveError(f'整张图中 "{v_sig.name}" 与 "{r_sig.name}" 从未在 {clock.name} 边沿发生握手')

    return actual


def expect_stable(
    d: Diagram,
    sig: Signal | str,
    start: TimeRef,
    end: TimeRef,
    *,
    no_x: bool = True,
) -> Value:
    """Assert ``sig`` stays constant (and non-x/z when ``no_x=True``) throughout ``[start, end)``."""
    s = d._signal_ref(sig)
    t0 = d._resolve(start)
    t1 = d._resolve(end)
    if t0 < 0:
        raise WaveError(f"expect_stable 的开始时间不能是负数，收到的是 {start!r}")
    if t1 <= t0:
        raise WaveError("expect_stable 的结束时间必须晚于开始时间")

    ts = d.timescale
    initial = s.value_at(t0)
    if no_x and initial in ("x", "z"):
        raise WaveError(f'信号 "{s.name}" 在稳定窗口起点 {format_time(t0, ts)} 为 "{initial}"（要求非 x/z）')

    for c in s.changes:
        if t0 < c.time < t1:
            if no_x and c.value in ("x", "z"):
                raise WaveError(
                    f'信号 "{s.name}" 在稳定窗口 [{format_time(t0, ts)}, {format_time(t1, ts)}) 内'
                    f'于 {format_time(c.time, ts)} 变为 "{c.value}"（要求非 x/z）'
                )
            if type(c.value) is not type(initial) or c.value != initial:
                raise WaveError(
                    f'信号 "{s.name}" 在稳定窗口 [{format_time(t0, ts)}, {format_time(t1, ts)}) 内发生了跳变：'
                    f"{format_time(c.time, ts)} 处 {s.display(initial)}→{s.display(c.value)}"
                )

    return initial


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
        if item.get("reason") == "inside_gap":
            layout_issues.append(
                f'总线丢字：{item["signal"]}@{item["start"]}~{item["end"]} 值 "{item["value"]}" 完全落在 gap 省略区间内未能显示'
            )
        else:
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
    for item in stats.crowded_regions:
        layout_issues.append(
            f'区域拥挤：{item["row"]}@{item["start"]}~{item["end"]} "{item["label"]}" 与其他文字存在 {item["overlaps"]} 处重叠'
        )
    if stats.hit_width_cap and (stats.truncated_labels or stats.dropped_labels):
        cap_px = int(d.max_width) if d.max_width and d.max_width == int(d.max_width) else d.max_width
        bus_drv = next((drv for drv in stats.width_drivers if drv["kind"] == "bus"), None)
        driver_hint = (
            f'（主要由 {bus_drv["signal"]}="{bus_drv["value"]}" 触发，至少需 {bus_drv["min_width"]:g}px）'
            if bus_drv
            else ""
        )
        layout_issues.append(
            f"画布已达 {cap_px}px 自动宽度上限{driver_hint}；可传 Diagram(max_width=None) 放开自适应宽度、"
            "传 d.bus(..., pad=False) 省略前导零，或用 d.gap(...) 折叠长空闲"
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
            "tco": format_time(c.tco, ts),
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
            "pad": s.pad if s.kind == "bus" else None,
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

    step_data: list[dict[str, Any]] = []
    for step in sorted(d.steps, key=lambda s: s.time):
        step_changes: list[dict[str, Any]] = []
        for sig, _old, new in step.changes:
            before = sig.value_before(step.time)
            if type(before) is type(new) and before == new:
                continue
            step_changes.append(
                {
                    "signal": sig.name,
                    "old": sig.display(before),
                    "new": sig.display(new),
                }
            )
        if step_changes:
            step_data.append(
                {
                    "time": format_time(step.time, ts),
                    "cause": step.cause,
                    "changes": step_changes,
                }
            )

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
        "font": {"families": list(d._fonts.families), "source": d._fonts.source},
        "end": format_time(end, ts),
        "canvas": {
            "width": stats.width,
            "height": stats.height,
            "px_per_ns": round(stats.px_per_ns, 4),
            "target_width": stats.target_width,
            "hit_width_cap": stats.hit_width_cap,
            "width_drivers": stats.width_drivers,
            "truncated_labels": stats.truncated_labels,
            "dropped_labels": stats.dropped_labels,
            "crowded_arrows": stats.crowded_arrows,
            "crowded_notes": stats.crowded_notes,
            "crowded_regions": stats.crowded_regions,
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
    font_tag = "+".join(info["font"]["families"]) if info["font"]["families"] else "default"
    lines = [
        f"[waveview check] {status} | {canvas['width']:g}x{canvas['height']:g}px | "
        f"theme={info['theme']} | font={font_tag} | end={info['end']} | svg={'valid' if info['svg_valid'] else 'INVALID'}"
    ]
    if canvas["target_width"] is not None and canvas["width_drivers"]:
        reasons = "、".join(_format_width_driver(drv) for drv in canvas["width_drivers"])
        lines.append(f"宽度自适应：目标 {canvas['target_width']:g}px -> 实际 {canvas['width']:g}px（{reasons}）")
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
