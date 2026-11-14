from fractions import Fraction

import pytest

from waveview import Diagram, WaveError, WaveWarning

NS = Fraction(1, 10**9)


def make():
    d = Diagram()
    clk = d.clock("clk", period="10ns")
    req = d.wire("req", init=0)
    data = d.bus("data", width=8)
    return d, clk, req, data


# ---- clocks and the cursor ----


def test_first_posedge_is_at_time_zero():
    d, clk, *_ = make()
    assert d.posedge(clk)._time == 0


def test_posedge_moves_strictly_forward():
    d, clk, *_ = make()
    d.posedge(clk)
    assert d.posedge(clk)._time == 10 * NS
    assert d.posedge(clk, 3)._time == 40 * NS


def test_negedge_follows_duty():
    d = Diagram()
    clk = d.clock("clk", period="10ns", duty=0.3)
    assert d.negedge(clk)._time == 3 * NS


def test_phase_shifts_edges():
    d = Diagram()
    clk = d.clock("clk", freq="100MHz", phase="90deg")
    assert d.posedge(clk)._time == Fraction(5, 2) * NS


def test_async_edges_are_exact_far_out():
    d = Diagram()
    clk = d.clock("clk", freq="143MHz", phase="2ns")
    t = d.posedge(clk, 1000)._time
    assert t == 2 * NS + Fraction(999, 143 * 10**6)


def test_mixed_clocks_interleave():
    d = Diagram()
    a = d.clock("a", period="10ns")
    b = d.clock("b", period="7ns", phase="2ns")
    d.posedge(a, 2)  # 10ns
    assert d.posedge(b)._time == 16 * NS
    assert d.negedge(a)._time == 25 * NS


def test_delay_and_at():
    d, clk, req, _ = make()
    d.posedge(clk, 2)
    assert d.delay("1ns")._time == 11 * NS
    assert d.at("63ns")._time == 63 * NS


def test_at_backwards_warns():
    d, clk, *_ = make()
    d.at("50ns")
    with pytest.warns(WaveWarning, match="往回走"):
        d.at("10ns")


# ---- assignment ----


def test_assign_and_hold():
    d, clk, req, data = make()
    d.posedge(clk, 2)(req=1, data=0xA5)
    d.posedge(clk, 3)(req=0)
    assert req.value_at(5 * NS) == 0
    assert req.value_at(25 * NS) == 1
    assert req.value_at(50 * NS) == 0
    assert data.value_at(50 * NS) == 0xA5  # never reassigned: holds


def test_set_before_any_wait_is_time_zero():
    d, clk, req, _ = make()
    d.set(req=1)
    assert req.changes[0].time == 0


def test_unknown_signal_suggests_close_name():
    d, clk, *_ = make()
    with pytest.raises(WaveError, match='你是不是想写 "req"'):
        d.posedge(clk)(rqe=1)


def test_clock_cannot_be_assigned():
    d, clk, *_ = make()
    with pytest.raises(WaveError, match="时钟"):
        d.set(clk=1)


@pytest.mark.parametrize("bad", [2, "high", 0.5, None])
def test_wire_rejects_bad_values(bad):
    d, clk, *_ = make()
    with pytest.raises(WaveError):
        d.set(req=bad)


def test_bus_width_overflow():
    d, clk, *_ = make()
    with pytest.raises(WaveError, match="8 位"):
        d.set(data=256)


def test_double_assignment_same_instant():
    d, clk, *_ = make()
    d.posedge(clk)(req=1)
    with pytest.raises(WaveError, match="两次"):
        d.set(req=0)


def test_no_op_assignment_warns():
    d, clk, *_ = make()
    with pytest.warns(WaveWarning, match="不会产生变化"):
        d.posedge(clk)(req=0)


def test_x_and_z_are_case_insensitive():
    d, clk, req, _ = make()
    d.posedge(clk)(req="Z")
    assert req.value_at(0) == "z"


# ---- declarations ----


def test_freq_and_period_exclusive():
    d = Diagram()
    with pytest.raises(WaveError):
        d.clock("a", freq="1MHz", period="1us")
    with pytest.raises(WaveError):
        d.clock("b")


def test_bad_names():
    d = Diagram()
    with pytest.raises(WaveError, match="label"):
        d.bus("data[7:0]")
    d.wire("a")
    with pytest.raises(WaveError, match="已经声明"):
        d.wire("a")


def test_groups_are_recorded():
    d = Diagram()
    with d.group("outer"):
        with d.group("inner"):
            s = d.wire("s")
    assert s.groups == ("outer", "inner")


# ---- annotations ----


def test_event_handle_points_at_transition():
    d, clk, req, _ = make()
    e = d.posedge(clk, 2)(req=1)
    p = e.req
    assert p.signal is req and p.time == 10 * NS


def test_event_handle_rejects_unassigned_signal():
    d, clk, *_ = make()
    e = d.posedge(clk)(req=1)
    with pytest.raises(WaveError, match="没有给信号"):
        e.data


def test_arrow_needs_transitions():
    d, clk, *_ = make()
    e = d.posedge(clk)(req=1)
    with pytest.raises(WaveError, match="e1.req"):
        d.arrow(e, e.req)


def test_marker_defaults_to_now():
    d, clk, *_ = make()
    d.posedge(clk, 3)
    d.marker(label="T")
    assert d.annotations[-1].time == 20 * NS


def test_auto_end_leaves_two_fastest_periods():
    d, clk, req, _ = make()
    d.posedge(clk, 4)(req=1)
    assert d.end == 50 * NS


def test_gap_advances_cursor():
    d, clk, *_ = make()
    d.posedge(clk)
    d.gap("1us")
    assert d.posedge(clk)._time == 1010 * NS


def test_theme_names_come_from_palettes():
    from waveview.themes import THEMES

    base = set(THEMES["light"])
    for name, palette in THEMES.items():
        assert set(palette) == base, f"theme {name} 的配色键和 light 不一致"


def test_unknown_theme_is_rejected():
    with pytest.raises(WaveError, match="bamboo"):
        Diagram(theme="sakura")


def test_diagram_parameter_validation():
    for bad_end in (0, "-5ns", -10):
        with pytest.raises(WaveError, match="end"):
            Diagram(end=bad_end)
    for bad_px in (0, -2, float("nan"), float("inf"), True):
        with pytest.raises(WaveError, match="px_per_ns"):
            Diagram(px_per_ns=bad_px)
    d = Diagram()
    for bad_scale in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(WaveError, match="scale"):
            d.to_png(scale=bad_scale)
    with pytest.raises(WaveError, match="分组"):
        with d.group("   "):
            pass


def test_set_advances_cursor_from_none_to_zero():
    d, clk, req, _ = make()
    d.set(req=1)
    assert req.changes[0].time == 0
    e = d.posedge(clk)(req=0)
    assert e._time == 10 * NS


def test_warn_noop_false_and_segments_merge():
    import warnings

    d = Diagram(warn_noop=False)
    clk = d.clock("clk", period="10ns")
    req = d.wire("req", init=0)
    data = d.bus("data", init=0xA5, width=8)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        d.posedge(clk)(req=0, data=0xA5)
        d.posedge(clk)(req=0, data=0xA5)
    assert not rec
    assert req.segments(d.end) == [(Fraction(0), d.end, 0)]
    assert data.segments(d.end) == [(Fraction(0), d.end, 0xA5)]


def test_cross_diagram_and_self_arrow_rejected():
    d1, clk1, req1, _ = make()
    d2, clk2, req2, _ = make()
    e1 = d1.posedge(clk1)(req=1)
    e2 = d2.posedge(clk2)(req=1)

    with pytest.raises(WaveError, match="不属于当前图表"):
        d2.posedge(clk1)
    with pytest.raises(WaveError, match="不属于当前图表"):
        d2.region(req1, "0ns", "10ns")
    with pytest.raises(WaveError, match="不属于当前图表"):
        d2.arrow(e1.req, e2.req)
    with pytest.raises(WaveError, match="同一个信号跳变"):
        d1.arrow(e1.req, e1.req)


def test_wait_attr_and_event_ergonomics():
    d, clk, req, data = make()
    w = d.posedge(clk)
    with pytest.raises(WaveError, match="需要先调用赋值"):
        _ = w.req
    e = w(req=1)
    assert req in e
    assert "req" in e
    assert data not in e
    assert "data" not in e
    assert e[req] == e.req


def test_describe_out_of_order_at_computes_accurate_old():
    d = Diagram()
    req = d.wire("req", init=0)
    with pytest.warns():
        d.at("20ns")(req=0)  # written first while still 0
        d.at("10ns")(req=1)  # later inserts earlier 0->1
    desc = d.describe()
    assert "req 0→1" in desc
    assert "req 1→0" in desc


def test_gated_clock_windows_and_idle():
    d = Diagram()
    sclk = d.clock("sclk", period="20ns", idle=0, start="20ns", stop="70ns")
    assert sclk.level_at(0) == 0
    assert sclk.level_at(20 * NS) == 1
    assert sclk.level_at(30 * NS) == 0
    assert sclk.level_at(70 * NS) == 0

    assert d.posedge(sclk)._time == 20 * NS
    assert d.negedge(sclk)._time == 30 * NS
    assert d.posedge(sclk)._time == 40 * NS
    assert d.posedge(sclk)._time == 60 * NS
    assert d.negedge(sclk)._time == 70 * NS
    with pytest.raises(WaveError, match="窗口"):
        d.posedge(sclk)


def test_gated_clock_enable_disable_and_idle_high():
    d = Diagram()
    scl = d.clock("scl", period="20ns", phase="10ns", idle=1, gated=True)
    assert scl.level_at(0) == 1
    scl.enable("20ns")
    assert scl.level_at(20 * NS) == 0
    assert d.negedge(scl)._time == 20 * NS  # falls from idle=1 at window start
    assert d.posedge(scl)._time == 30 * NS
    assert d.negedge(scl)._time == 40 * NS  # falls at 40ns; stays 0 on [40, 45)
    scl.disable("45ns")
    assert scl.level_at(45 * NS) == 1
    assert d.posedge(scl)._time == 45 * NS  # rises back to idle=1 at window end

    d.at("80ns")
    scl.enable()
    assert d.posedge(scl)._time == 90 * NS
    d.at("105ns")  # [100, 110) is raw 0 -> returns to idle=1 with a rising edge
    scl.disable()
    edges = scl.edges_between(0, 120 * NS)
    assert (80 * NS, "fall") in edges
    assert (105 * NS, "rise") in edges


def test_gated_clock_validation_and_negative_annotations():
    d = Diagram()
    with pytest.raises(WaveError, match="idle"):
        d.clock("a", period="10ns", idle=True)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="start"):
        d.clock("b", period="10ns", start="-1ns")
    with pytest.raises(WaveError, match="stop"):
        d.clock("c", period="10ns", start="20ns", stop="20ns")
    sig = d.wire("sig", init=0)
    with pytest.raises(WaveError, match="负数"):
        d.marker(at="-1ns")
    with pytest.raises(WaveError, match="负数"):
        d.region(sig, "-1ns", "5ns")
    with pytest.raises(WaveError, match="负数"):
        d.measure("-1ns", "5ns")
