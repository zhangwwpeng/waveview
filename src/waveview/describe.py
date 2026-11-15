"""describe(): the diagram as a plain-text list of events, in time order."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .model import Arrow, Clock, Marker, Measure, Note, Region, Signal
from .units import format_freq, format_time

if TYPE_CHECKING:
    from .diagram import Diagram


def describe(d: Diagram) -> str:
    ts = d.timescale

    def t(x) -> str:
        return format_time(x, ts)

    lines = []
    if d.title:
        lines.append(f"标题：{d.title}")

    clocks = [r for r in d.rows if isinstance(r, Clock)]
    if clocks:
        parts = []
        for c in clocks:
            extra = f" phase={t(c.phase)}" if c.phase else ""
            if c.duty != 0.5:
                extra += f" duty={float(c.duty):g}"
            if c._gated:
                if c.windows:
                    win = "、".join(
                        f"{t(ws)}~{t(we) if we is not None else '∞'}" for ws, we in c.windows
                    )
                else:
                    win = "关闭"
                extra += f" 门控({win}, idle={c.idle})"
            parts.append(f"{c.name} {format_freq(c.freq)}（周期 {t(c.period)}）{extra}")
        lines.append("时钟：" + "；".join(parts))

    signals = [r for r in d.rows if isinstance(r, Signal)]
    if signals:
        init = " ".join(f"{s.name}={s.display(s.init)}" for s in signals)
        lines.append(f"t={t(0):<10} 初始        {init}")

    for step in sorted(d.steps, key=lambda s: s.time):
        changes = ", ".join(
            f"{sig.name} {sig.display(sig.value_before(step.time))}→{sig.display(new)}"
            for sig, _old, new in step.changes
        )
        lines.append(f"t={t(step.time):<10} {step.cause:<11} {changes}")

    for g in d.gaps:
        lines.append(f"省略：{t(g.start)} ~ {t(g.end)}")

    for a in d.annotations:
        label = f' "{a.label}"' if a.label else ""
        if isinstance(a, Arrow):
            lines.append(
                f"箭头：{a.src.signal.name}@{t(a.src.time)} → {a.dst.signal.name}@{t(a.dst.time)}{label}"
            )
        elif isinstance(a, Region):
            lines.append(f"区域：{a.row.name} {t(a.start)} ~ {t(a.end)}{label}")
        elif isinstance(a, Marker):
            lines.append(f"标记：{t(a.time)}{label}")
        elif isinstance(a, Measure):
            lines.append(f"测量：{t(a.start)} ~ {t(a.end)}（{t(a.end - a.start)}）{label}")
        elif isinstance(a, Note):
            lines.append(f"注释：{a.row.name}@{t(a.time)}{label}")

    for cap in d.captions:
        lines.append(f"图注：{cap}")

    lines.append(f"结束：{t(d.end)}")
    return "\n".join(lines)
