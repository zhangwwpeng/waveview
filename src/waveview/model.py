"""The data model every front end writes and every back end reads."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Literal

from .errors import WaveError, warn

Value = int | str  # wire: 0 / 1 / "x" / "z"; bus: int, label, "x" / "z"
EdgeKind = Literal["rise", "fall"]


def _ceil(x: Fraction) -> int:
    return math.ceil(x)


def _resolve_row_time(diagram: Any, at: Any, negative_msg: str, require_at_msg: str | None = None) -> Fraction:
    if at is None and require_at_msg is not None:
        raise WaveError(require_at_msg)
    if diagram is not None:
        t = (diagram._now or Fraction(0)) if at is None else diagram._resolve(at)
    elif at is None:
        t = Fraction(0)
    elif isinstance(at, Fraction):
        t = at
    else:
        from .units import parse_time

        t = parse_time(at)
    if t < 0:
        raise WaveError(negative_msg)
    return t


@dataclass(eq=False)
class Clock:
    name: str
    label: str
    period: Fraction
    phase: Fraction  # first rising edge, normalised into [0, period)
    duty: Fraction
    groups: tuple[str, ...] = ()
    idle: Literal[0, 1] = 0
    windows: list[tuple[Fraction, Fraction | None]] = field(default_factory=list)
    tco: Fraction = Fraction(0)
    _gated: bool = False
    _diagram: Any = field(default=None, repr=False)

    def __repr__(self) -> str:
        from .units import format_freq

        return f"Clock({self.name!r}, {format_freq(self.freq)})"

    @property
    def freq(self) -> Fraction:
        return 1 / self.period

    def _resolve_time(self, at: Any) -> Fraction:
        return _resolve_row_time(
            self._diagram,
            at,
            f'时钟 "{self.name}" 的开关时间不能是负数，收到的是 {at!r}',
        )

    def _nth_edge_time(self, kind: EdgeKind, n: int) -> Fraction:
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise WaveError(f"边沿序号必须是正整数（从 1 开始），收到的是 {n!r}")
        cur: Fraction | None = None
        for _ in range(n):
            cur, _ = self.next_edge(kind, cur)
        assert cur is not None
        return cur

    def posedge(self, n: int = 1) -> Point:
        """Non-mutating Point at the 1-based ``n``-th rising edge of this clock."""
        return Point(self, self._nth_edge_time("rise", n))

    def negedge(self, n: int = 1) -> Point:
        """Non-mutating Point at the 1-based ``n``-th falling edge of this clock."""
        return Point(self, self._nth_edge_time("fall", n))

    def at(self, time: Any) -> Point:
        """Non-mutating Point on this clock at ``time`` (no cursor mutation)."""
        t = _resolve_row_time(
            self._diagram,
            time,
            f'时钟 "{self.name}" 的锚点时间不能是负数，收到的是 {time!r}',
            require_at_msg="at() 需要传入时间或事件，若取当前时间请写 d.point(row)",
        )
        return Point(self, t)

    def _add_window(self, ws: Fraction, we: Fraction | None) -> None:
        if we is not None and we <= ws:
            return
        raw = sorted([*self.windows, (ws, we)], key=lambda w: w[0])
        merged: list[tuple[Fraction, Fraction | None]] = []
        for s, e in raw:
            if merged and (merged[-1][1] is None or s <= merged[-1][1]):
                ps, pe = merged[-1]
                ne = None if (pe is None or e is None) else max(pe, e)
                merged[-1] = (ps, ne)
            else:
                merged.append((s, e))
        self.windows = merged

    def enable(self, at: Any = None) -> Clock:
        """Enable this clock from ``at`` (defaults to the current cursor) onwards."""
        t = self._resolve_time(at)
        if not self._gated:
            if t > 0:
                from .units import format_time

                ts = self._diagram.timescale if self._diagram is not None else "ns"
                t_str = format_time(t, ts)
                warn(
                    f'时钟 "{self.name}" 声明时未设 gated=True，调用 enable({t_str}) '
                    f"会将 {t_str} 之前的时钟沿全部屏蔽（建议在 clock() 中加上 gated=True）"
                )
            self._gated = True
            self.windows = [(t, None)]
        else:
            self._add_window(t, None)
        return self

    def disable(self, at: Any = None) -> Clock:
        """Gate off this clock from ``at`` (defaults to the current cursor) onwards."""
        t = self._resolve_time(at)
        if not self._gated:
            self._gated = True
            self.windows = [(Fraction(0), t)] if t > 0 else []
        else:
            kept: list[tuple[Fraction, Fraction | None]] = []
            for ws, we in self.windows:
                if ws < t:
                    kept.append((ws, t if (we is None or we > t) else we))
            self.windows = kept
        return self

    def _base(self, kind: EdgeKind) -> Fraction:
        return self.phase if kind == "rise" else self.phase + self.duty * self.period

    def _first_k(self, kind: EdgeKind) -> int:
        """The k of the first edge of this kind at or after time 0."""
        return _ceil((0 - self._base(kind)) / self.period)

    def edge(self, kind: EdgeKind, k: int) -> Fraction:
        return self._base(kind) + k * self.period

    def _raw_level(self, t: Fraction) -> int:
        pos = (t - self.phase) % self.period
        return 1 if pos < self.duty * self.period else 0

    def _raw_level_before(self, t: Fraction) -> int:
        pos = (t - self.phase) % self.period
        if pos == 0:
            return 0
        return 1 if pos <= self.duty * self.period else 0

    def _active_at(self, t: Fraction) -> bool:
        return any(ws <= t and (we is None or t < we) for ws, we in self.windows)

    def _level_before(self, t: Fraction) -> int:
        if self._gated:
            if not any(ws < t and (we is None or t <= we) for ws, we in self.windows):
                return self.idle
        return self._raw_level_before(t)

    def _edge_kind_at(self, t: Fraction) -> EdgeKind | None:
        before = self._level_before(t)
        after = self.level_at(t)
        if before == 0 and after == 1:
            return "rise"
        if before == 1 and after == 0:
            return "fall"
        return None

    def _count_gated_edges(self, kind: EdgeKind, limit: Fraction) -> int:
        base = self._base(kind)
        total = 0
        for ws, we in self.windows:
            if ws > limit:
                break
            if self._edge_kind_at(ws) == kind:
                total += 1
            if we is not None and we <= limit:
                k_min = math.floor((ws - base) / self.period) + 1
                k_max = _ceil((we - base) / self.period) - 1
                if k_max >= k_min:
                    total += k_max - k_min + 1
                if self._edge_kind_at(we) == kind:
                    total += 1
            else:
                k_min = math.floor((ws - base) / self.period) + 1
                k_max = math.floor((limit - base) / self.period)
                if k_max >= k_min:
                    total += k_max - k_min + 1
        return total

    def next_edge(self, kind: EdgeKind, after: Fraction | None) -> tuple[Fraction, int]:
        """The first edge strictly after ``after`` (or at/after 0 when ``after`` is None).

        Returns the edge time and its 1-based index counted from time 0.
        """
        if not self._gated:
            base = self._base(kind)
            if after is None:
                k = self._first_k(kind)
            else:
                k = math.floor((after - base) / self.period) + 1
                k = max(k, self._first_k(kind))
            return self.edge(kind, k), k - self._first_k(kind) + 1

        base = self._base(kind)
        for ws, we in self.windows:
            if we is not None and after is not None and we <= after:
                continue
            candidates: list[Fraction] = []
            if (after is None and ws >= 0) or (after is not None and ws > after):
                candidates.append(ws)
            lower = ws if after is None else max(ws, after)
            k = math.floor((lower - base) / self.period) + 1
            raw_t = base + k * self.period
            if raw_t > ws and (we is None or raw_t < we):
                candidates.append(raw_t)
            if we is not None and (after is None or we > after):
                candidates.append(we)
            for t in sorted(set(candidates)):
                if self._edge_kind_at(t) == kind:
                    return t, self._count_gated_edges(kind, t)

        label = "上升" if kind == "rise" else "下降"
        raise WaveError(f'门控时钟 "{self.name}" 后续没有可用的{label}沿（请检查 enable/disable 或 start/stop 窗口）')

    def level_at(self, t: Fraction) -> int:
        if self._gated and not self._active_at(t):
            return self.idle
        return self._raw_level(t)

    def edges_between(self, start: Fraction, end: Fraction) -> list[tuple[Fraction, EdgeKind]]:
        """All edges in (start, end], in time order."""
        if not self._gated:
            out: list[tuple[Fraction, EdgeKind]] = []
            for kind in ("rise", "fall"):
                base = self._base(kind)
                k = math.floor((start - base) / self.period) + 1
                while (t := base + k * self.period) <= end:
                    out.append((t, kind))
                    k += 1
            out.sort(key=lambda e: e[0])
            return out

        cand_times: set[Fraction] = set()
        for ws, we in self.windows:
            if ws > end:
                break
            if we is not None and we <= start:
                continue
            if start < ws <= end:
                cand_times.add(ws)
            hi = end if we is None else min(end, we)
            lo = max(start, ws)
            for kind in ("rise", "fall"):
                base = self._base(kind)
                k = math.floor((lo - base) / self.period) + 1
                while (t := base + k * self.period) <= hi:
                    cand_times.add(t)
                    k += 1
            if we is not None and start < we <= end:
                cand_times.add(we)

        out = []
        for t in sorted(cand_times):
            kind = self._edge_kind_at(t)
            if kind is not None:
                out.append((t, kind))
        return out


@dataclass
class Change:
    time: Fraction
    value: Value


@dataclass(eq=False)
class Signal:
    name: str
    label: str
    kind: Literal["wire", "bus"]
    init: Value
    width: int | None = None
    fmt: Literal["hex", "dec", "bin"] = "hex"
    groups: tuple[str, ...] = ()
    changes: list[Change] = field(default_factory=list)
    pad: bool = True
    _diagram: Any = field(default=None, repr=False)

    def __repr__(self) -> str:
        return f"Signal({self.name!r}, {self.kind})"

    def at(self, time: Any) -> Point:
        """Non-mutating Point on this signal at ``time`` (no cursor mutation)."""
        t = _resolve_row_time(
            self._diagram,
            time,
            f'信号 "{self.name}" 的锚点时间不能是负数，收到的是 {time!r}',
            require_at_msg="at() 需要传入时间或事件，若取当前时间请写 d.point(row)",
        )
        return Point(self, t)

    def value_at(self, t: Fraction) -> Value:
        value = self.init
        for c in self.changes:
            if c.time <= t:
                value = c.value
            else:
                break
        return value

    def value_before(self, t: Fraction) -> Value:
        value = self.init
        for c in self.changes:
            if c.time < t:
                value = c.value
            else:
                break
        return value

    def change_at(self, t: Fraction) -> Change | None:
        for c in self.changes:
            if c.time == t:
                return c
        return None

    def add(self, t: Fraction, value: Value) -> None:
        self.changes.append(Change(t, value))
        self.changes.sort(key=lambda c: c.time)

    def segments(self, end: Fraction) -> list[tuple[Fraction, Fraction, Value]]:
        """Constant-value intervals covering [0, end], collapsing consecutive equal values."""
        points = [(Fraction(0), self.init)] + [(c.time, c.value) for c in self.changes if c.time > 0]
        if self.changes and self.changes[0].time == 0:
            points[0] = (Fraction(0), self.changes[0].value)
        out: list[tuple[Fraction, Fraction, Value]] = []
        for i, (t, v) in enumerate(points):
            if t >= end:
                break
            t_next = min(points[i + 1][0] if i + 1 < len(points) else end, end)
            if t_next <= t:
                continue
            if out and type(out[-1][2]) is type(v) and out[-1][2] == v:
                prev_t, _, prev_v = out[-1]
                out[-1] = (prev_t, t_next, prev_v)
            else:
                out.append((t, t_next, v))
        return out

    def display(self, value: Value) -> str:
        if isinstance(value, int) and self.kind == "bus":
            if self.fmt == "dec":
                return str(value)
            if self.fmt == "bin":
                return "0b" + format(value, f"0{self.width}b" if (self.width and self.pad) else "b")
            digits = (self.width + 3) // 4 if (self.width and self.pad) else 1
            return "0x" + format(value, f"0{digits}X")
        return str(value)


Row = Clock | Signal


@dataclass(frozen=True)
class Point:
    """A point on a signal or clock at one instant, used by ``arrow()`` / ``note()``."""

    signal: Row
    time: Fraction

    @property
    def row(self) -> Row:
        return self.signal

    def __repr__(self) -> str:
        from .units import format_time

        return f"Point({self.signal.name}.{format_time(self.time)})"


@dataclass
class Arrow:
    src: Point
    dst: Point
    label: str | None


@dataclass
class Region:
    row: Row
    start: Fraction
    end: Fraction
    label: str | None


@dataclass
class Marker:
    time: Fraction
    label: str | None


@dataclass
class Measure:
    start: Fraction
    end: Fraction
    label: str | None


@dataclass
class Note:
    row: Row
    time: Fraction
    label: str


@dataclass
class Gap:
    start: Fraction
    end: Fraction


@dataclass
class Step:
    """One assignment statement, kept for describe()."""

    time: Fraction
    cause: str
    changes: list[tuple[Signal, Value, Value]]  # signal, old, new
    edge_clock: Clock | None = None
    edge_time: Fraction | None = None


Annotation = Arrow | Region | Marker | Measure | Note
