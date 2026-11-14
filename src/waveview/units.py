"""Parse and format times, frequencies and phases.

All times are kept as exact ``Fraction`` seconds so that edges of unrelated
clocks never drift, however far the diagram runs.
"""

from __future__ import annotations

import math
import re
from fractions import Fraction

from .errors import WaveError

TIME_UNITS = {
    "fs": Fraction(1, 10**15),
    "ps": Fraction(1, 10**12),
    "ns": Fraction(1, 10**9),
    "us": Fraction(1, 10**6),
    "ms": Fraction(1, 10**3),
    "s": Fraction(1),
}

FREQ_UNITS = {
    "hz": Fraction(1),
    "khz": Fraction(10**3),
    "mhz": Fraction(10**6),
    "ghz": Fraction(10**9),
}

_DIGITS = r"\d(?:_?\d)*"
_NUMBER_UNIT = re.compile(
    rf"^\s*([+-]?(?:{_DIGITS}\.(?:{_DIGITS})?|\.{_DIGITS}|{_DIGITS})(?:[eE][+-]?{_DIGITS})?)\s*([A-Za-z\u00b5\u03bc]*)\s*$"
)

Number = int | float | Fraction


def _split(text: str, what: str) -> tuple[Fraction, str]:
    m = _NUMBER_UNIT.match(text)
    if not m:
        raise WaveError(f'{what} "{text}" 格式不对，应该写成数字加单位，比如 "10ns"')
    return Fraction(m.group(1).replace("_", "")), m.group(2)


def _number(value: Number) -> Fraction:
    if isinstance(value, bool):
        raise WaveError(f"需要数字，收到的是 {value!r}")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WaveError(f"需要有限数字，收到的是 {value!r}")
        return Fraction(str(value))
    return Fraction(value)


def _normalise_unit(unit: str) -> str:
    return unit.replace("\u00b5", "u").replace("\u03bc", "u").lower()


def parse_time(value: str | Number, default_unit: str = "ns") -> Fraction:
    """``"2.5ns"`` -> seconds. A bare number is read in ``default_unit``."""
    if isinstance(value, str):
        number, unit = _split(value, "时间")
        unit = _normalise_unit(unit) or default_unit.lower()
        if unit not in TIME_UNITS:
            raise WaveError(f'时间 "{value}" 的单位 "{unit}" 不认识，可用：{", ".join(TIME_UNITS)}')
        return number * TIME_UNITS[unit]
    if isinstance(value, (int, float, Fraction)):
        return _number(value) * TIME_UNITS[default_unit]
    raise WaveError(f"时间需要写成字符串（如 \"10ns\"）或数字，收到的是 {value!r}")


def parse_freq(value: str | Number) -> Fraction:
    """``"100MHz"`` -> Hz. A bare number is read in Hz."""
    if isinstance(value, str):
        number, unit = _split(value, "频率")
        unit = (unit or "hz").lower()
        if unit not in FREQ_UNITS:
            raise WaveError(f'频率 "{value}" 的单位不认识，可用：Hz、kHz、MHz、GHz')
        freq = number * FREQ_UNITS[unit]
    else:
        freq = _number(value)
    if freq <= 0:
        raise WaveError(f"频率必须大于 0，收到的是 {value!r}")
    return freq


def parse_phase(value: str | Number, period: Fraction, default_unit: str = "ns") -> Fraction:
    """A time, or an angle such as ``"90deg"`` or ``"90°"`` (a fraction of ``period``)."""
    if isinstance(value, str):
        s = value.strip()
        if s.lower().endswith("deg"):
            body = s[:-3]
        elif s.endswith("°"):
            body = s[:-1]
        else:
            return parse_time(value, default_unit)
        number, unit = _split(body, "相位")
        if unit:
            raise WaveError(f'相位 "{value}" 的格式不对，角度请写成数字加 deg 或 °，比如 "90deg"')
        return period * number / 360
    return parse_time(value, default_unit)


def format_time(seconds: Fraction, unit: str = "ns", digits: int = 3) -> str:
    """Seconds -> ``"5.497ns"``, trailing zeros dropped."""
    value = seconds / TIME_UNITS[unit]
    text = f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return f"{text}{unit}"


def format_freq(hz: Fraction) -> str:
    for unit, scale in (("GHz", 10**9), ("MHz", 10**6), ("kHz", 10**3)):
        if hz >= scale:
            text = f"{float(hz / scale):.3f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
    return f"{float(hz):g}Hz"
