"""The Diagram API: a Verilog-testbench-like way to describe a timing diagram.

    d = Diagram()
    clk = d.clock("clk", freq="100MHz")
    req = d.wire("req", init=0)
    d.posedge(clk)(req=1)        # @(posedge clk) req <= 1;
"""

from __future__ import annotations

import difflib
import keyword
import math
import os
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any, Generator, Literal

from .errors import WaveError, warn
from .model import (
    Annotation,
    Arrow,
    Clock,
    Gap,
    Marker,
    Measure,
    Note,
    Point,
    Region,
    Row,
    Signal,
    Step,
    Value,
)
from .themes import THEMES
from .units import TIME_UNITS, format_time, parse_freq, parse_phase, parse_time


class Instant:
    """A bare point in time, as returned by ``d.now``."""

    def __init__(self, time: Fraction):
        self._time = time

    def __repr__(self) -> str:
        return f"Instant({format_time(self._time)})"


class Event:
    """What one assignment did: ``e.req`` is req's transition at that instant."""

    def __init__(self, time: Fraction, signals: dict[str, Signal]):
        self._time = time
        self._signals = signals

    def __contains__(self, item: object) -> bool:
        if isinstance(item, Signal):
            return any(sig is item for sig in self._signals.values())
        if isinstance(item, str):
            return item in self._signals
        return False

    def __getattr__(self, name: str) -> Point:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, key: str | Signal) -> Point:
        if isinstance(key, Signal):
            for sig in self._signals.values():
                if sig is key:
                    return Point(sig, self._time)
            assigned = "、".join(self._signals) or "（无）"
            raise WaveError(f'这个事件没有给信号 "{key.name}" 赋值，它赋值的信号是：{assigned}')
        signal = self._signals.get(key)
        if signal is None:
            assigned = "、".join(self._signals) or "（无）"
            raise WaveError(f'这个事件没有给信号 "{key}" 赋值，它赋值的信号是：{assigned}')
        return Point(signal, self._time)

    def __repr__(self) -> str:
        return f"Event({format_time(self._time)}, {', '.join(self._signals)})"


class Wait:
    """Where a wait statement landed. Call it to assign at that instant."""

    def __init__(self, diagram: Diagram, time: Fraction, cause: str):
        self._diagram = diagram
        self._time = time
        self._cause = cause

    def __call__(self, **values: Value) -> Event:
        return self._diagram._assign(self._time, self._cause, values)

    def __getattr__(self, name: str) -> Point:
        if name.startswith("_"):
            raise AttributeError(name)
        raise WaveError(
            f"d.posedge()/delay()/at() 等等待语句需要先调用赋值才能访问信号 .{name}，"
            f"例如：e = d.posedge(clk)({name}=1); ... e.{name}"
        )

    def __repr__(self) -> str:
        return f"Wait({format_time(self._time)}, {self._cause})"


TimeRef = Event | Point | Instant | Wait | str | int | float | Fraction
PointRef = Point | tuple[Row | str, TimeRef]


def _positive_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Fraction)):
        raise WaveError(f"{name} 必须是有限正数，收到的是 {value!r}")
    num = float(value)
    if not math.isfinite(num) or num <= 0:
        raise WaveError(f"{name} 必须是有限正数，收到的是 {value!r}")
    return num


class Diagram:
    def __init__(
        self,
        title: str | None = None,
        timescale: str = "ns",
        end: str | int | float | None = None,
        theme: str = "light",
        px_per_ns: float | None = None,
        warn_noop: bool = True,
        max_width: float | None = 2400.0,
    ):
        if timescale not in TIME_UNITS:
            raise WaveError(f'timescale "{timescale}" 不认识，可用：{", ".join(TIME_UNITS)}')
        if theme not in THEMES:
            names = "、".join(THEMES)
            raise WaveError(f"theme 只能是 {names} 之一，收到的是 {theme!r}")
        if px_per_ns is not None:
            px_per_ns = _positive_finite(px_per_ns, "px_per_ns")
        if max_width is not None:
            max_width = _positive_finite(max_width, "max_width")
        self.title = title
        self.timescale = timescale
        self.theme = theme
        self.px_per_ns = px_per_ns
        self.warn_noop = warn_noop
        self.max_width: float | None = max_width
        self._end = None if end is None else self._time(end)
        if self._end is not None and self._end <= 0:
            raise WaveError(f"end 必须大于 0，收到的是 {end!r}")

        self.rows: list[Row] = []
        self.annotations: list[Annotation] = []
        self.captions: list[str] = []
        self.gaps: list[Gap] = []
        self.steps: list[Step] = []

        self._names: dict[str, Row] = {}
        self._groups: list[str] = []
        self._now: Fraction | None = None  # None: before time 0
        self._cause = "初始"

    # ---- declarations -------------------------------------------------

    def _declare(self, name: str) -> None:
        if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
            raise WaveError(
                f"名字 {name!r} 不是合法的 Python 标识符；"
                f'需要显示特殊字符时用 label，比如 d.bus("data", label="data[7:0]")'
            )
        if name in self._names:
            raise WaveError(f'名字 "{name}" 已经声明过了')

    def clock(
        self,
        name: str,
        freq: str | int | float | None = None,
        period: str | int | float | None = None,
        phase: str | int | float = 0,
        duty: float = 0.5,
        label: str | None = None,
        idle: Literal[0, 1] = 0,
        start: TimeRef | None = None,
        stop: TimeRef | None = None,
        gated: bool = False,
        tco: str | int | float | Fraction = 0,
    ) -> Clock:
        self._declare(name)
        if (freq is None) == (period is None):
            raise WaveError(f'时钟 "{name}"：freq 和 period 必须写一个，并且只能写一个')
        p = 1 / parse_freq(freq) if freq is not None else self._time(period)  # type: ignore[arg-type]
        if p <= 0:
            raise WaveError(f'时钟 "{name}"：周期必须大于 0')
        if isinstance(duty, bool) or not isinstance(duty, (int, float, Fraction)) or (
            isinstance(duty, float) and not math.isfinite(duty)
        ):
            raise WaveError(f'时钟 "{name}"：duty 必须是 0 和 1 之间的有限小数，收到的是 {duty!r}')
        d = Fraction(str(duty)) if isinstance(duty, float) else Fraction(duty)
        if not 0 < d < 1:
            raise WaveError(f'时钟 "{name}"：duty 必须在 0 和 1 之间（不含），收到的是 {duty}')
        if isinstance(idle, bool) or idle not in (0, 1):
            raise WaveError(f'时钟 "{name}"：idle 只能是 0 或 1，收到的是 {idle!r}')
        if isinstance(tco, bool) or not isinstance(tco, (str, int, float, Fraction)):
            raise WaveError(f'时钟 "{name}"：tco 必须是有效的时间，收到的是 {tco!r}')
        tco_val = self._time(tco)
        if tco_val < 0 or tco_val >= p:
            raise WaveError(
                f'时钟 "{name}"：tco 必须大于等于 0 且小于时钟周期 {format_time(p, self.timescale)}'
            )
        ph = parse_phase(phase, p, self.timescale) % p

        s_t = None if start is None else self._resolve(start)
        e_t = None if stop is None else self._resolve(stop)
        if s_t is not None and s_t < 0:
            raise WaveError(f'时钟 "{name}"：start 不能是负数，收到的是 {start!r}')
        s_bound = Fraction(0) if s_t is None else s_t
        if e_t is not None and e_t <= s_bound:
            raise WaveError(f'时钟 "{name}"：stop 必须大于 start')
        is_gated = gated or (s_t is not None) or (e_t is not None)
        windows: list[tuple[Fraction, Fraction | None]] = []
        if s_t is not None or e_t is not None:
            windows = [(s_bound, e_t)]

        clk = Clock(
            name,
            label or name,
            p,
            ph,
            d,
            tuple(self._groups),
            idle=idle,
            windows=windows,
            tco=tco_val,
            _gated=is_gated,
            _diagram=self,
        )
        self.rows.append(clk)
        self._names[name] = clk
        return clk

    def wire(self, name: str, init: Value = "x", label: str | None = None) -> Signal:
        self._declare(name)
        sig = Signal(name, label or name, "wire", "x", groups=tuple(self._groups), _diagram=self)
        sig.init = self._normalise(sig, init)
        self.rows.append(sig)
        self._names[name] = sig
        return sig

    def bus(
        self,
        name: str,
        init: Value = "x",
        width: int | None = None,
        fmt: Literal["hex", "dec", "bin"] = "hex",
        label: str | None = None,
        pad: bool = True,
    ) -> Signal:
        self._declare(name)
        if width is not None and (not isinstance(width, int) or width < 1):
            raise WaveError(f'总线 "{name}"：width 必须是正整数，收到的是 {width!r}')
        if fmt not in ("hex", "dec", "bin"):
            raise WaveError(f'总线 "{name}"：fmt 只能是 "hex"、"dec" 或 "bin"')
        if not isinstance(pad, bool):
            raise WaveError(f'总线 "{name}"：pad 必须是布尔值，收到的是 {pad!r}')
        sig = Signal(
            name,
            label or name,
            "bus",
            "x",
            width,
            fmt,
            tuple(self._groups),
            pad=pad,
            _diagram=self,
        )
        sig.init = self._normalise(sig, init)
        self.rows.append(sig)
        self._names[name] = sig
        return sig

    @contextmanager
    def group(self, name: str) -> Generator[None]:
        if not isinstance(name, str) or not name.strip():
            raise WaveError(f"分组名称必须是非空字符串，收到的是 {name!r}")
        self._groups.append(name)
        try:
            yield
        finally:
            self._groups.pop()

    # ---- the cursor ---------------------------------------------------

    @property
    def now(self) -> Instant:
        return Instant(self._now or Fraction(0))

    def _clock(self, clk: Clock | str) -> Clock:
        if isinstance(clk, str):
            row = self._names.get(clk)
            if not isinstance(row, Clock):
                raise WaveError(f'"{clk}" 不是已声明的时钟')
            return row
        if not isinstance(clk, Clock):
            raise WaveError(f"需要一个时钟（d.clock(...) 的返回值），收到的是 {clk!r}")
        if not any(r is clk for r in self.rows):
            raise WaveError(f'时钟 "{clk.name}" 不属于当前图表')
        return clk

    def _edge(
        self,
        clk: Clock | str,
        n: int,
        kind: Literal["rise", "fall"],
        tco: str | int | float | Fraction | None = None,
    ) -> Wait:
        clk = self._clock(clk)
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise WaveError(f"等待的边沿数必须是正整数，收到的是 {n!r}")
        if tco is None:
            dt = clk.tco
        else:
            if isinstance(tco, bool) or not isinstance(tco, (str, int, float, Fraction)):
                raise WaveError(f"tco 必须是有效的时间，收到的是 {tco!r}")
            dt = self._time(tco)
            if dt < 0:
                raise WaveError(f"tco 不能是负数，收到的是 {tco!r}")
        now, index = self._now, 0
        for _ in range(n):
            now, index = clk.next_edge(kind, now)
        self._now = now + dt
        self._cause = f"{clk.name}{'↑' if kind == 'rise' else '↓'}#{index}"
        if dt > 0:
            self._cause += f"+{format_time(dt, self.timescale)}"
        return Wait(self, self._now, self._cause)

    def posedge(
        self,
        clk: Clock | str,
        n: int = 1,
        *,
        tco: str | int | float | Fraction | None = None,
    ) -> Wait:
        """@(posedge clk); ``n`` > 1 is repeat(n) @(posedge clk)."""
        return self._edge(clk, n, "rise", tco=tco)

    def negedge(
        self,
        clk: Clock | str,
        n: int = 1,
        *,
        tco: str | int | float | Fraction | None = None,
    ) -> Wait:
        """@(negedge clk); ``n`` > 1 is repeat(n) @(negedge clk)."""
        return self._edge(clk, n, "fall", tco=tco)

    def delay(self, t: str | int | float) -> Wait:
        """#t: move the cursor forward by ``t``."""
        dt = self._time(t)
        if dt < 0:
            raise WaveError(f"delay 不能是负数，收到的是 {t!r}")
        self._now = (self._now or Fraction(0)) + dt
        self._cause = f"+{format_time(dt, self.timescale)}"
        return Wait(self, self._now, self._cause)

    def at(self, t: str | int | float) -> Wait:
        """Jump to an absolute time, e.g. for an asynchronous reset."""
        target = self._time(t)
        if target < 0:
            raise WaveError(f"时间不能是负数，收到的是 {t!r}")
        if self._now is not None and target < self._now:
            warn(
                f"d.at({t!r}) 让时间从 {format_time(self._now, self.timescale)} 往回走了；"
                "图照样能画，但请确认这是有意的"
            )
        self._now = target
        self._cause = f"@{format_time(target, self.timescale)}"
        return Wait(self, self._now, self._cause)

    def gap(self, t: str | int | float) -> Wait:
        """Like delay(t), but the interval is drawn squeezed into a break mark."""
        dt = self._time(t)
        if dt <= 0:
            raise WaveError(f"gap 必须大于 0，收到的是 {t!r}")
        start = self._now or Fraction(0)
        self.gaps.append(Gap(start, start + dt))
        self._now = start + dt
        self._cause = f"+{format_time(dt, self.timescale)}（省略）"
        return Wait(self, self._now, self._cause)

    def set(self, **values: Value) -> Event:
        """Assign at the current time: ``d.set(req=1, data="0xA5")``."""
        if self._now is None:
            self._now = Fraction(0)
        return self._assign(self._now, self._cause, values)

    # ---- assignment ---------------------------------------------------

    def _signal(self, name: str) -> Signal:
        row = self._names.get(name)
        if isinstance(row, Signal):
            return row
        if isinstance(row, Clock):
            raise WaveError(f'"{name}" 是时钟，时钟会自动生成，不能赋值')
        signals = [n for n, r in self._names.items() if isinstance(r, Signal)]
        hint = difflib.get_close_matches(name, signals, n=1)
        msg = f'信号 "{name}" 不存在'
        if hint:
            msg += f'，你是不是想写 "{hint[0]}"？'
        else:
            msg += f"。已声明的信号：{'、'.join(signals) or '（无）'}"
        raise WaveError(msg)

    def _normalise(self, sig: Signal, value: Value) -> Value:
        if isinstance(value, str) and value.strip().lower() in ("x", "z"):
            return value.strip().lower()
        if sig.kind == "wire":
            if isinstance(value, bool):
                return int(value)
            if value in (0, 1) and not isinstance(value, float):
                return int(value)
            if value in ("0", "1"):
                return int(value)
            raise WaveError(f'wire "{sig.name}" 的值只能是 0、1、"x"、"z"，收到的是 {value!r}')
        if isinstance(value, bool) or isinstance(value, float):
            raise WaveError(f'总线 "{sig.name}" 的值要写成整数或字符串，收到的是 {value!r}')
        if isinstance(value, int):
            if value < 0:
                raise WaveError(f'总线 "{sig.name}" 的整数值不能是负数，收到的是 {value}')
            if sig.width is not None and value >= 1 << sig.width:
                raise WaveError(
                    f'总线 "{sig.name}" 是 {sig.width} 位，装不下 {value}（{hex(value)}）'
                )
            return value
        if isinstance(value, str):
            return value
        raise WaveError(f'总线 "{sig.name}" 的值要写成整数或字符串，收到的是 {value!r}')

    def _assign(self, t: Fraction, cause: str, values: dict[str, Value]) -> Event:
        if not values:
            raise WaveError("赋值时至少要给一个信号，比如 d.set(req=1)")
        signals: dict[str, Signal] = {}
        changes = []
        for name, raw in values.items():
            sig = self._signal(name)
            value = self._normalise(sig, raw)
            if sig.change_at(t) is not None:
                raise WaveError(
                    f'信号 "{name}" 在 {format_time(t, self.timescale)} 已经赋值过一次了，'
                    "同一时刻不能赋值两次"
                )
            old = sig.value_at(t)
            if old == value and self.warn_noop:
                warn(
                    f'信号 "{name}" 在 {format_time(t, self.timescale)} 本来就是 {value!r}，'
                    "这次赋值不会产生变化"
                )
            sig.add(t, value)
            signals[name] = sig
            changes.append((sig, old, value))
        self.steps.append(Step(t, cause, changes))
        return Event(t, signals)

    # ---- annotations --------------------------------------------------

    def _time(self, value: str | int | float | Fraction) -> Fraction:
        return parse_time(value, self.timescale)

    def _resolve(self, ref: TimeRef) -> Fraction:
        if isinstance(ref, (Event, Point, Instant, Wait)):
            return ref._time if not isinstance(ref, Point) else ref.time
        if isinstance(ref, (str, int, float, Fraction)) and not isinstance(ref, bool):
            return self._time(ref)
        raise WaveError(f"需要一个时间点（事件、时间字符串或 d.now），收到的是 {ref!r}")

    def _row(self, row: Row | str) -> Row:
        if isinstance(row, str):
            found = self._names.get(row)
            if found is None:
                raise WaveError(f'"{row}" 不是已声明的信号或时钟')
            return found
        if not isinstance(row, (Clock, Signal)):
            raise WaveError(f"需要一个信号或时钟，收到的是 {row!r}")
        if not any(r is row for r in self.rows):
            raise WaveError(f'信号或时钟 "{row.name}" 不属于当前图表')
        return row

    def point(self, row: Row | str, at: TimeRef | None = None) -> Point:
        """Return a non-mutating Point anchor on ``row`` at ``at`` (defaults to ``d.now``)."""
        r = self._row(row)
        t = (self._now or Fraction(0)) if at is None else self._resolve(at)
        if t < 0:
            raise WaveError("point 的时间不能是负数")
        return Point(r, t)

    def _point_ref(self, ref: PointRef, end_name: str) -> Point:
        if isinstance(ref, Point):
            if not any(r is ref.signal for r in self.rows):
                raise WaveError(f'箭头的{end_name}目标 "{ref.signal.name}" 不属于当前图表')
            if ref.time < 0:
                raise WaveError(f"箭头的{end_name}时间不能是负数")
            return ref
        if isinstance(ref, tuple) and len(ref) == 2:
            row_ref, time_ref = ref
            r = self._row(row_ref)
            t = self._resolve(time_ref)
            if t < 0:
                raise WaveError(f"箭头的{end_name}时间不能是负数")
            return Point(r, t)
        raise WaveError(
            f"箭头的{end_name}要写成信号跳变、边沿锚点或 (信号, 时间) 二元组"
            f"（如 e1.req、clk.posedge(2) 或 (ready, e3)），收到的是 {ref!r}"
        )

    def arrow(self, src: PointRef, dst: PointRef, label: str | None = None) -> None:
        """An arrow from one point/edge to another: ``d.arrow(e1.req, (ack, e2))`` or ``d.arrow(clk.posedge(2), e.q)``."""
        p_src = self._point_ref(src, "起点")
        p_dst = self._point_ref(dst, "终点")
        if p_src == p_dst:
            raise WaveError("箭头的起点和终点不能是同一个信号跳变")
        self.annotations.append(Arrow(p_src, p_dst, label))

    def region(self, row: Row | str, start: TimeRef, end: TimeRef, label: str | None = None) -> None:
        """Highlight ``row`` between ``start`` and ``end``."""
        a, b = self._resolve(start), self._resolve(end)
        if a < 0:
            raise WaveError("region 的开始时间不能是负数")
        if b <= a:
            raise WaveError("region 的结束时间必须晚于开始时间")
        self.annotations.append(Region(self._row(row), a, b, label))

    def marker(self, at: TimeRef | None = None, label: str | None = None) -> None:
        """A dashed vertical line across every row; ``at`` defaults to now."""
        t = (self._now or Fraction(0)) if at is None else self._resolve(at)
        if t < 0:
            raise WaveError("marker 的时间不能是负数")
        self.annotations.append(Marker(t, label))

    def measure(self, start: TimeRef, end: TimeRef, label: str | None = None) -> None:
        """A dimension line between two instants."""
        a, b = self._resolve(start), self._resolve(end)
        if a < 0:
            raise WaveError("measure 的开始时间不能是负数")
        if b <= a:
            raise WaveError("measure 的结束时间必须晚于开始时间")
        self.annotations.append(Measure(a, b, label))

    def note(
        self,
        target: Point | Row | str,
        text_or_at: str | TimeRef,
        text: str | None = None,
    ) -> None:
        """Attach a single-point callout note: ``d.note(e.ready, "...")`` or ``d.note(clk, "20ns", "...")``."""
        if isinstance(target, Point):
            if text is not None:
                raise WaveError('传入事件跳变点（如 e.sig）时它已自带时间，请直接写 d.note(e.sig, "说明")')
            if not isinstance(text_or_at, str) or not text_or_at.strip():
                raise WaveError("note 的注释文字必须是非空字符串")
            if not any(r is target.signal for r in self.rows):
                raise WaveError(f'note 的目标信号 "{target.signal.name}" 不属于当前图表')
            if target.time < 0:
                raise WaveError("note 的时间不能是负数")
            self.annotations.append(Note(target.signal, target.time, text_or_at.strip()))
            return

        row = self._row(target)
        if text is None:
            raise WaveError(
                '在信号或时钟上添加 note 需要指定时间，例如 d.note(sig, d.now, "说明")，'
                '或直接传跳变点 d.note(e.sig, "说明")'
            )
        if not isinstance(text, str) or not text.strip():
            raise WaveError("note 的注释文字必须是非空字符串")
        t = self._resolve(text_or_at)
        if t < 0:
            raise WaveError("note 的时间不能是负数")
        self.annotations.append(Note(row, t, text.strip()))

    def caption(self, *texts: str) -> None:
        """Add one or more explanatory footer lines below the time axis."""
        if not texts:
            raise WaveError("caption 至少需要传入一条非空注释文字")
        added = 0
        for item in texts:
            if not isinstance(item, str):
                raise WaveError(f"caption 的内容必须是字符串，收到的是 {item!r}")
            for raw_line in item.splitlines():
                line = raw_line.strip()
                if line:
                    self.captions.append(line)
                    added += 1
        if added == 0:
            raise WaveError("caption 的注释文字不能为空")

    # ---- finishing ----------------------------------------------------

    def _last_time(self) -> Fraction:
        times = [Fraction(0)]
        times += [c.time for r in self.rows if isinstance(r, Signal) for c in r.changes]
        times += [
            t
            for r in self.rows
            if isinstance(r, Clock)
            for ws, we in r.windows
            for t in (ws, we)
            if t is not None
        ]
        times += [g.end for g in self.gaps]
        if self._now is not None:
            times.append(self._now)
        for a in self.annotations:
            if isinstance(a, Arrow):
                times += [a.src.time, a.dst.time]
            elif isinstance(a, (Marker, Note)):
                times.append(a.time)
            else:
                times.append(a.end)
        return max(times)

    @property
    def end(self) -> Fraction:
        if self._end is not None:
            return self._end
        last = self._last_time()
        clocks = [r for r in self.rows if isinstance(r, Clock)]
        if clocks:
            return last + 2 * min(c.period for c in clocks)
        return last + max(last / 10, TIME_UNITS[self.timescale] * 10)

    def check(self) -> None:
        """Warnings about a diagram that draws but probably isn't what was meant."""
        if self._end is not None and self._last_time() > self._end:
            warn(f"有事件或标注超出了 end={format_time(self._end, self.timescale)}，超出的部分不会画出来")
        for r in self.rows:
            if isinstance(r, Signal) and not r.changes and r.init == "x":
                warn(f'信号 "{r.name}" 从来没被赋值过，初始值也是 "x"，整根都会画成未知')

    def describe(self) -> str:
        from .describe import describe

        return describe(self)

    def sample(
        self,
        clk: Clock | str,
        edge: Literal["rise", "fall"] = "rise",
        *,
        include_gaps: bool = False,
    ) -> list[dict[str, Any]]:
        """Sample all signals at every active clock edge within the diagram."""
        from .inspect import sample_clock

        return sample_clock(self, clk, edge, include_gaps=include_gaps)

    def to_dict(self) -> dict[str, Any]:
        """Return structured timing semantics, SVG diagnostics, and clock samples."""
        from .inspect import to_dict

        return to_dict(self)

    def inspect(self, fmt: Literal["text", "json"] = "text") -> str:
        """Return an AI-friendly self-check and per-edge timing report."""
        from .inspect import inspect_diagram

        return inspect_diagram(self, fmt=fmt)

    def to_svg(self) -> str:
        from .svg import render

        self.check()
        return render(self)

    def _repr_svg_(self) -> str:
        return self.to_svg()

    def to_png(self, scale: float = 2) -> bytes:
        from .png import svg_to_png

        scale_f = _positive_finite(scale, "scale")
        return svg_to_png(self.to_svg(), scale_f)

    def save(self, path: str | os.PathLike[str], scale: float = 2) -> None:
        """Write ``.svg`` or ``.png`` by the file's extension."""
        out = Path(path)
        lower = str(out).lower()
        if lower.endswith(".svg"):
            svg = self.to_svg()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(svg, encoding="utf-8")
        elif lower.endswith(".png"):
            png = self.to_png(scale)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(png)
        else:
            raise WaveError(f"不知道怎么保存 {path!r}：文件扩展名要是 .svg 或 .png")
