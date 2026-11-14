from fractions import Fraction

import pytest

from waveview import WaveError
from waveview.units import format_time, parse_freq, parse_phase, parse_time

NS = Fraction(1, 10**9)


@pytest.mark.parametrize(
    "text, expected",
    [("10ns", 10 * NS), ("2.5ns", Fraction(5, 2) * NS), ("1 us", 1000 * NS), ("500ps", NS / 2), (3, 3 * NS)],
)
def test_parse_time(text, expected):
    assert parse_time(text) == expected


def test_bare_number_uses_default_unit():
    assert parse_time(4, "us") == 4000 * NS


def test_bare_number_string_uses_default_unit():
    assert parse_time("10") == 10 * NS


@pytest.mark.parametrize("bad", ["ten ns", "10 parsecs", None])
def test_parse_time_rejects(bad):
    with pytest.raises(WaveError):
        parse_time(bad)


def test_parse_freq():
    assert parse_freq("100MHz") == 10**8
    assert parse_freq("1.5GHz") == Fraction(15, 10) * 10**9
    with pytest.raises(WaveError):
        parse_freq("0MHz")


def test_parse_phase_angle_and_time():
    period = 8 * NS
    assert parse_phase("90deg", period) == 2 * NS
    assert parse_phase("3ns", period) == 3 * NS


def test_format_time():
    assert format_time(Fraction(1, 143 * 10**6), "ns") == "6.993ns"
    assert format_time(Fraction(0)) == "0ns"
    assert format_time(20 * NS) == "20ns"


def test_parse_time_extensions():
    assert parse_time("1_000ns") == 1000 * NS
    assert parse_time("10NS") == 10 * NS
    assert parse_time("1.25µs") == Fraction(1250) * NS
    assert parse_time("1.25μs") == Fraction(1250) * NS


@pytest.mark.parametrize("bad", ["1__0ns", "1_ns", "_1ns", "10µ", float("nan"), float("inf"), float("-inf"), True])
def test_parse_time_rejects_edge_cases(bad):
    with pytest.raises(WaveError):
        parse_time(bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "0_Hz"])
def test_parse_freq_rejects_edge_cases(bad):
    with pytest.raises(WaveError):
        parse_freq(bad)


def test_parse_phase_strict_units_and_degree_symbol():
    period = 8 * NS
    assert parse_phase("90°", period) == 2 * NS
    assert parse_phase("90 DEG", period) == 2 * NS
    for bad in ("90foodeg", "90 foo°", "deg", "°"):
        with pytest.raises(WaveError):
            parse_phase(bad, period)
