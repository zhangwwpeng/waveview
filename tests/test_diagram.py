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


def test_note_and_caption_api_and_validation():
    import warnings
    from waveview.model import Note

    d1, clk1, req1, data1 = make()
    d2, clk2, req2, _ = make()

    e1 = d1.posedge(clk1)(req=1, data=0x12)
    e2 = d1.posedge(clk1)(req=0)
    d1.note(e1.req, "拉高请求")
    d1.note(clk1, "20ns", "采样沿")
    d1.note("data", d1.now, "数据保持")
    d1.caption("注 1：valid 与 ready 同拍采样。", "注 2：第二行\n注 3：多行自动拆分")

    notes = [a for a in d1.annotations if isinstance(a, Note)]
    assert [(n.row.name, n.time, n.label) for n in notes] == [
        ("req", 0, "拉高请求"),
        ("clk", 20 * NS, "采样沿"),
        ("data", 10 * NS, "数据保持"),
    ]
    assert d1.captions == ["注 1：valid 与 ready 同拍采样。", "注 2：第二行", "注 3：多行自动拆分"]

    # Out-of-end note contributes to _last_time() and emits WaveWarning when clipped.
    d1_tight = Diagram(end="15ns")
    c_tight = d1_tight.clock("clk", period="10ns")
    r_tight = d1_tight.wire("req", init=0)
    d1_tight.posedge(c_tight)(req=1)
    d1_tight.note(r_tight, "30ns", "越界注释")
    assert d1_tight._last_time() == 30 * NS
    with pytest.warns(UserWarning, match="超出了"):
        d1_tight.check()

    # Validation errors for note()
    e_other = d2.posedge(clk2)(req=1)
    with pytest.raises(WaveError, match="非空字符串"):
        d1.note(e1.req, "   ")
    with pytest.raises(WaveError, match="自带时间"):
        d1.note(e1.req, "10ns", "重复时间")  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="不属于当前图表"):
        d1.note(e_other.req, "跨图")
    with pytest.raises(WaveError, match="指定时间"):
        d1.note(req1, "缺少时间")
    with pytest.raises(WaveError, match="非空字符串"):
        d1.note(req1, "10ns", "")
    with pytest.raises(WaveError, match="负数"):
        d1.note(req1, "-5ns", "负时间")
    with pytest.raises(WaveError, match="不属于当前图表"):
        d1.note(clk2, "10ns", "跨图时钟")

    # Validation errors for caption()
    with pytest.raises(WaveError, match="至少需要"):
        d1.caption()
    with pytest.raises(WaveError, match="字符串"):
        d1.caption(123)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="不能为空"):
        d1.caption("   \n  ")
    with warnings.catch_warnings(record=True):
        pass


def test_non_mutating_anchors_and_clock_arrow():
    d = Diagram()
    clk = d.clock("clk", period="10ns", phase="2ns")
    sclk = d.clock("sclk", period="20ns", idle=0, start="15ns", stop="60ns")
    sig = d.wire("sig", init=0)

    d.at("5ns")
    p_rise2 = clk.posedge(2)
    p_fall1 = clk.negedge(1)
    p_sig = sig.at("18ns")
    p_clk_at = clk.at("30ns")
    p_diag = d.point("sig")
    p_gated = sclk.posedge(2)

    # None of the above may move the cursor d.now!
    assert d.now._time == 5 * NS
    assert p_rise2.signal is clk and p_rise2.row is clk and p_rise2.time == 12 * NS
    assert p_fall1.signal is clk and p_fall1.time == 7 * NS
    assert p_sig.signal is sig and p_sig.time == 18 * NS
    assert p_clk_at.time == 30 * NS
    assert p_diag.signal is sig and p_diag.time == 5 * NS
    # Gated sclk: [0, 15ns) idle=0; [10, 20) raw is 0, so rise #1=20ns, rise #2=40ns
    assert p_gated.time == 40 * NS

    e = d.at("14ns")(sig=1)
    d.arrow(clk.posedge(2), e.sig, "t_co")
    d.note(clk.posedge(3), "第3拍")
    d.measure(clk.posedge(1), clk.posedge(3), "2周期")

    with pytest.raises(WaveError, match="正整数"):
        clk.posedge(0)
    with pytest.raises(WaveError, match="正整数"):
        clk.negedge(True)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="负数"):
        sig.at("-1ns")
    with pytest.raises(WaveError, match="负数"):
        d.point(clk, "-2ns")
    with pytest.raises(WaveError, match="at\\(\\)"):
        clk.at(None)


def test_clock_and_wait_tco_delay_semantics():
    d = Diagram()
    clk = d.clock("clk", period="10ns", tco="1.5ns")
    q = d.wire("q", init=0)

    e1 = d.posedge(clk)(q=1)
    assert e1._time == Fraction(3, 2) * NS
    assert q.value_at(1 * NS) == 0
    assert q.value_at(Fraction(3, 2) * NS) == 1

    e2 = d.posedge(clk, tco="2ns")(q=0)
    assert e2._time == 12 * NS

    e3 = d.negedge(clk, tco=0)(q=1)
    assert e3._time == 15 * NS

    desc = d.describe()
    assert "tco=1.5ns" in desc
    assert "clk↑#1+1.5ns" in desc
    assert "clk↑#2+2ns" in desc
    assert "clk↓#2 " in desc

    # Sampling at clock rising edges still sees registered behavior properly:
    samples = d.sample(clk)
    assert samples[0]["signals"]["q"] == {"sampled": "0", "after": "1", "changed": True}
    assert samples[1]["signals"]["q"] == {"sampled": "1", "after": "0", "changed": True}
    assert samples[2]["signals"]["q"] == {"sampled": "1", "after": "1", "changed": False}

    # Validation errors on tco
    with pytest.raises(WaveError, match="tco"):
        d.clock("bad1", period="10ns", tco="-1ns")
    with pytest.raises(WaveError, match="tco"):
        d.clock("bad2", period="10ns", tco="10ns")
    with pytest.raises(WaveError, match="tco"):
        d.clock("bad3", period="10ns", tco=True)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="负数"):
        d.posedge(clk, tco="-0.5ns")


def test_arrow_tuple_refs_and_noop_filtering():
    d = Diagram(warn_noop=False)
    clk = d.clock("clk", period="10ns")
    valid = d.wire("valid", init=0)
    ready = d.wire("ready", init=0)

    d.posedge(clk)
    e1 = d.posedge(clk)(valid=1)      # 10ns
    e2 = d.posedge(clk)(ready=1)      # 20ns
    e3 = d.posedge(clk)(valid=0)      # 30ns: handshake (ready stays 1)
    w_hs = d.posedge(clk)             # 40ns: uncalled Wait
    d.set(ready=0)                    # 40ns
    e_noop = d.posedge(clk)(ready=0)  # 50ns: noop, still returns e_noop.ready Point

    d.arrow(e1.valid, (ready, e3), "握手")
    d.arrow(e2.ready, ("ready", w_hs), "释放")
    assert e_noop.ready.time == 50 * NS

    desc = d.describe()
    assert "箭头：valid@10ns → ready@30ns \"握手\"" in desc
    assert "箭头：ready@20ns → ready@40ns \"释放\"" in desc
    assert "ready 0→0" not in desc
    assert "clk↑#6" not in desc

    steps = d.to_dict()["steps"]
    assert all(all(ch["old"] != ch["new"] for ch in st["changes"]) for st in steps)

    with pytest.raises(WaveError, match="二元组"):
        d.arrow(e1.valid, (ready,))  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="负数"):
        d.arrow(e1.valid, (ready, "-1ns"))


def test_bus_pad_and_diagram_max_width():
    d = Diagram(max_width=None)
    bus_hex = d.bus("wdata", init=0, width=32, pad=False)
    bus_bin = d.bus("flags", init=5, width=8, fmt="bin", pad=False)
    d.at("10ns")(wdata=0x270F)

    assert bus_hex.display(0) == "0x0"
    assert bus_hex.display(0x270F) == "0x270F"
    assert bus_bin.display(5) == "0b101"

    with pytest.raises(WaveError, match="装不下"):
        d.at("20ns")(wdata=1 << 32)
    with pytest.raises(WaveError, match="负数"):
        d.at("30ns")(wdata=-1)
    with pytest.raises(WaveError, match="pad"):
        d.bus("bad_pad", width=8, pad=1)  # type: ignore[arg-type]
    for bad_max_width in (0, -100, float("nan"), float("inf"), True):
        with pytest.raises(WaveError, match="max_width"):
            Diagram(max_width=bad_max_width)  # type: ignore[arg-type]
    for bad_width in (0, -100, float("nan"), float("inf"), True):
        with pytest.raises(WaveError, match="width"):
            Diagram(width=bad_width)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="不能同时指定"):
        Diagram(width=900, px_per_ns=8)


def test_assign_is_atomic_on_error_and_allows_self_signal():
    d, clk, req, data = make()
    with pytest.raises(WaveError, match="不存在"):
        d.set(req=1, nosuch=1)
    assert req.changes == []
    assert d.steps == []

    # Retrying at the same instant after failed partial call succeeds cleanly.
    d.set(req=1)
    assert req.value_at(0) == 1

    # Signal named `self` can be assigned via both d.set() and Wait.__call__().
    s = d.wire("self", init=0)
    e0 = d.set(self=1)
    e1 = d.posedge(clk)(self=0)
    assert s.value_at(0) == 1
    assert e0.self.time == 0
    assert s.value_at(10 * NS) == 0
    assert e1.self.time == 10 * NS


def test_per_wait_tco_upper_bound_and_label_validation():
    d, clk, req, _ = make()
    with pytest.raises(WaveError, match="周期"):
        d.posedge(clk, tco="10ns")
    with pytest.raises(WaveError, match="周期"):
        d.negedge(clk, tco="15ns")

    with pytest.raises(WaveError, match="字符串"):
        Diagram(title=123)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="控制字符"):
        Diagram(title="bad\x00title")
    with pytest.raises(WaveError, match="字符串"):
        d.wire("bad_w", label=7)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="非空"):
        d.bus("bad_b", label="   ")
    with pytest.raises(WaveError, match="字符串"):
        d.clock("bad_c", period="10ns", label=42)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="控制字符"):
        d.group("bad\x01grp").__enter__()
    e = d.posedge(clk)(req=1)
    e2 = d.posedge(clk)(req=0)
    with pytest.raises(WaveError, match="字符串"):
        d.marker(e, label=99)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="字符串"):
        d.region(req, e, e2, label=99)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="字符串"):
        d.measure(e, e2, label=99)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="字符串"):
        d.arrow(e.req, e2.req, label=99)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match="控制字符"):
        d.note(e.req, "bad\x00note")
    with pytest.raises(WaveError, match="控制字符"):
        d.caption("bad\x07cap")


def test_p3_api_consistency():
    # 1. Instant.started tracks cursor initialization; d.at() accepts TimeRef while delay/gap reject it.
    d, clk, req, data = make()
    assert d.now.started is False
    assert "unstarted" in repr(d.now)
    e0 = d.set(req=1)
    assert d.now.started is True
    assert repr(d.now) == "Instant(0ns)"
    d.at(clk.posedge(3))(req=0)
    assert req.value_at(20 * NS) == 0
    with pytest.warns(UserWarning, match="往回走了"):
        d.at(e0)
    with pytest.raises(WaveError, match=r"d\.at"):
        d.delay(e0)  # type: ignore[arg-type]
    with pytest.raises(WaveError, match=r"d\.at"):
        d.gap(clk.posedge(2))  # type: ignore[arg-type]

    # 2. Leading underscore rejected for signal/clock identifiers.
    with pytest.raises(WaveError, match="Event 内部属性"):
        d.wire("_priv")
    with pytest.raises(WaveError, match="Event 内部属性"):
        d.clock("_clk", period="10ns")

    # 3. Calling enable(at > 0) on non-gated clock emits WaveWarning.
    d_ungated = Diagram()
    clk_u = d_ungated.clock("clk", period="10ns")
    with pytest.warns(UserWarning, match="未设 gated=True"):
        clk_u.enable("20ns")

    # 4. Bus radix string literals parse like ints, check width, and coalesce with int values.
    d_bus = Diagram(warn_noop=False)
    c_bus = d_bus.clock("clk", period="10ns")
    b8 = d_bus.bus("b8", init="x", width=8)
    d_bus.posedge(c_bus)(b8="0xA5")
    d_bus.posedge(c_bus)(b8=0xA5)
    d_bus.posedge(c_bus)(b8="0b00001010")
    assert b8.value_at(0) == 0xA5
    assert len(b8.segments(d_bus.end)) == 2  # [0..20ns)=0xA5, [20ns..end)=0x0A
    assert "0xA5→0xA5" not in d_bus.describe()
    with pytest.raises(WaveError, match="8 位"):
        d_bus.posedge(c_bus)(b8="0x100")
    with pytest.raises(WaveError, match="缺少数字"):
        d_bus.posedge(c_bus)(b8="0x")
    d_bus.posedge(c_bus)(b8="IDLE")
    assert b8.value_at(50 * NS) == "IDLE"
