"""Render the examples: valid SVG, PNG bytes, and unchanged against snapshots.

Run with UPDATE_SNAPSHOTS=1 to accept new output after an intended change.
"""

import os
import runpy
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from waveview.themes import THEMES

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.py"))
SNAPSHOTS = Path(__file__).parent / "snapshots"


def load(path: Path):
    return runpy.run_path(str(path))["d"]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_svg_matches_snapshot(path):
    svg = load(path).to_svg()
    ET.fromstring(svg)  # well-formed
    snap = SNAPSHOTS / f"{path.stem}.svg"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOTS.mkdir(exist_ok=True)
        snap.write_text(svg, encoding="utf-8")
    elif not snap.exists():
        pytest.fail(f"Missing snapshot {snap.name}; run UPDATE_SNAPSHOTS=1 pytest to generate")
    assert svg == snap.read_text(encoding="utf-8"), f"{snap.name} changed; rerun with UPDATE_SNAPSHOTS=1 if intended"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_describe_matches_snapshot(path):
    text = load(path).describe()
    snap = SNAPSHOTS / f"{path.stem}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOTS.mkdir(exist_ok=True)
        snap.write_text(text, encoding="utf-8")
    elif not snap.exists():
        pytest.fail(f"Missing snapshot {snap.name}; run UPDATE_SNAPSHOTS=1 pytest to generate")
    assert text == snap.read_text(encoding="utf-8")


def test_png_is_png():
    png = load(ROOT / "examples" / "handshake.py").to_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_save_rejects_unknown_extension(tmp_path):
    from waveview import WaveError

    with pytest.raises(WaveError, match=".svg 或 .png"):
        load(ROOT / "examples" / "handshake.py").save(tmp_path / "x.jpg")


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_every_theme_renders(theme):
    from waveview import Diagram

    d = Diagram(title="theme", theme=theme)
    clk = d.clock("clk", period="10ns")
    d.wire("req", init=0)
    d.posedge(clk)(req=1)
    svg = d.to_svg()
    ET.fromstring(svg)
    assert THEMES[theme]["bg"] in svg
    assert f'id="wv-{theme}-hi-grad"' in svg
    assert f'url(#wv-{theme}-' not in svg or f'id="wv-{theme}-' in svg


def test_save_creates_parent_dirs_and_repr_svg(tmp_path):
    d = load(ROOT / "examples" / "handshake.py")
    assert d._repr_svg_() == d.to_svg()

    svg_out = tmp_path / "a" / "b" / "wave.svg"
    png_out = tmp_path / "c" / "d" / "wave.png"
    d.save(svg_out)
    d.save(png_out)
    assert svg_out.read_text(encoding="utf-8").startswith("<svg")
    assert png_out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_wire_zero_to_x_has_no_fake_wave_vertical_edge():
    from waveview import Diagram

    d = Diagram()
    clk = d.clock("clk", period="10ns")
    s = d.wire("s", init=0)
    d.posedge(clk, 2)(s="x")
    d.posedge(clk)(s=1)
    root = ET.fromstring(d.to_svg())
    wave_color = THEMES[d.theme]["wave"]
    wave_paths = [el.attrib["d"] for el in root.findall(".//{http://www.w3.org/2000/svg}path") if el.attrib.get("stroke") == wave_color]
    # Two disconnected traces: flat 0 segment and flat 1 segment, neither with V connecting top and bottom.
    assert len(wave_paths) == 2
    assert all("V" not in p for p in wave_paths)


def test_bus_scale_across_gap_keeps_value_untruncated():
    from waveview import Diagram

    d = Diagram()
    clk = d.clock("clk", period="10ns")
    data = d.bus("data", init="x")
    d.posedge(clk)(data="LONG_PAYLOAD_TOKEN")
    d.delay("5ns")
    d.gap("1us")
    d.posedge(clk)(data="x")
    svg = d.to_svg()
    ET.fromstring(svg)
    assert "LONG_PAYLOAD_TOKEN" in svg


def test_non_overlapping_measures_share_lane():
    from waveview import Diagram

    d = Diagram()
    clk = d.clock("clk", period="10ns")
    d.wire("a", init=0)
    t1 = d.posedge(clk, 2)(a=1)
    t2 = d.posedge(clk, 2)(a=0)
    t3 = d.posedge(clk, 3)(a=1)
    t4 = d.posedge(clk, 2)(a=0)
    d.measure(t1, t2, "M1")
    d.measure(t3, t4, "M2")
    root = ET.fromstring(d.to_svg())
    mh_lines = [
        el
        for el in root.findall(".//{http://www.w3.org/2000/svg}line")
        if "marker-end" in el.attrib and "mh" in el.attrib["marker-end"]
    ]
    assert len(mh_lines) == 2
    assert mh_lines[0].attrib["y1"] == mh_lines[1].attrib["y1"]


def test_overlapping_markers_stack_and_out_of_end_annotations_clip():
    from waveview import Diagram, WaveWarning

    d = Diagram(end="30ns")
    clk = d.clock("clk", period="10ns")
    sig = d.wire("sig", init=0)
    d.posedge(clk, 2)(sig=1)
    d.marker(at="10ns", label="FIRST_BADGE")
    d.marker(at="12ns", label="SECOND_BADGE")
    d.marker(at="50ns", label="BEYOND_END")
    with pytest.warns(WaveWarning):
        svg = d.to_svg()
    assert "BEYOND_END" not in svg
    root = ET.fromstring(svg)
    texts = {el.text: el.attrib["y"] for el in root.findall(".//{http://www.w3.org/2000/svg}text") if el.text}
    assert texts["FIRST_BADGE"] != texts["SECOND_BADGE"]


def test_zebra_stripe_indents_past_group_bracket():
    from waveview import Diagram

    d = Diagram()
    with d.group("grp"):
        d.wire("s0", init=0)
        d.wire("s1", init=1)
    root = ET.fromstring(d.to_svg())
    alt_rects = [
        el
        for el in root.findall(".//{http://www.w3.org/2000/svg}rect")
        if el.attrib.get("fill") == THEMES[d.theme]["row_alt"]
    ]
    assert len(alt_rects) == 1
    assert float(alt_rects[0].attrib["x"]) >= 28.0


def test_sub_millisecond_ticks_format_distinctly():
    from waveview import Diagram

    d = Diagram(timescale="ms", end="2us")
    sig = d.wire("sig", init=0)
    d.at("500ns")(sig=1)
    root = ET.fromstring(d.to_svg())
    muted = THEMES[d.theme]["muted"]
    ticks = [
        el.text
        for el in root.findall(".//{http://www.w3.org/2000/svg}text")
        if el.attrib.get("fill") == muted and el.attrib.get("text-anchor") == "middle"
    ]
    assert len(set(ticks)) >= 10
    assert "0.0005" in ticks


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_note_and_caption_render_across_themes_and_expand_canvas(theme):
    from waveview import Diagram
    from waveview.svg import render_with_stats

    def build(with_annotations: bool):
        d = Diagram(title="Note & Caption", theme=theme)
        clk = d.clock("clk", period="10ns")
        req = d.wire("req", init=0)
        data = d.bus("data", init="x", width=8)
        e1 = d.posedge(clk)(req=1, data=0x3C)
        e2 = d.posedge(clk)(req=0, data=0x7E)
        d.posedge(clk)(data="x")
        if with_annotations:
            d.note(clk, "10ns", "Clock & Edge")
            d.note(e1.req, "Assert <req>")
            d.note(req, "25ns", "Deasserted")
            d.note(data, "15ns", "Payload")
            d.caption(
                "Note 1: Valid transaction occurs when req == 1 & clk rises.",
                "Note 2: A very long explanatory footer line designed to expand canvas width cleanly.",
            )
        return d

    svg_plain, stats_plain = render_with_stats(build(False))
    svg_annotated, stats_annotated = render_with_stats(build(True))
    ET.fromstring(svg_plain)
    ET.fromstring(svg_annotated)

    assert "Assert &lt;req&gt;" in svg_annotated
    assert "Clock &amp; Edge" in svg_annotated
    assert "Note 1: Valid transaction occurs when req == 1 &amp; clk rises." in svg_annotated
    assert THEMES[theme]["note"] in svg_annotated
    assert stats_annotated.height > stats_plain.height
    assert stats_annotated.width > stats_plain.width
    assert stats_annotated.crowded_notes == []


def test_out_of_end_note_clips_with_warning():
    from waveview import Diagram, WaveWarning

    d = Diagram(end="20ns")
    clk = d.clock("clk", period="10ns")
    sig = d.wire("sig", init=0)
    d.posedge(clk)(sig=1)
    d.note(sig, "10ns", "VISIBLE_NOTE")
    d.note(sig, "40ns", "OUT_OF_RANGE_NOTE")
    with pytest.warns(WaveWarning):
        svg = d.to_svg()
    ET.fromstring(svg)
    assert "VISIBLE_NOTE" in svg
    assert "OUT_OF_RANGE_NOTE" not in svg


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_clock_anchor_arrow_note_and_tco_render_valid_svg(theme):
    from waveview import Diagram

    d = Diagram(title="Register Timing", theme=theme)
    clk = d.clock("clk", period="10ns", tco="1.5ns")
    d_in = d.wire("d", init=0)
    q_out = d.wire("q", init=0)

    e_d = d.at("7ns")(d=1)
    e_q = d.posedge(clk)(q=1)      # clk↑#2 @10ns -> 11.5ns
    d.at("13ns")(d=0)
    d.posedge(clk)(q=0)            # clk↑#3 @20ns -> 21.5ns

    d.measure(e_d, clk.posedge(2), "t_su")
    d.arrow(clk.posedge(2), e_q.q, "t_co")
    d.note(clk.posedge(2), "capture")
    d.note(clk.at("25ns"), "low")
    d.caption("Note: tco shifts registered Q transitions relative to clk posedge.")

    svg = d.to_svg()
    ET.fromstring(svg)
    assert "t_co" in svg
    assert "capture" in svg
    assert "t_su" in svg


def test_arrow_tuple_and_unpadded_32bit_bus_render_valid_svg():
    from waveview import Diagram
    from waveview.svg import render_with_stats

    d = Diagram(title="Unpadded 32-bit Handshake", theme="bamboo")
    clk = d.clock("clk", freq="100MHz")
    valid = d.wire("valid", init=0)
    ready = d.wire("ready", init=0)
    wdata = d.bus("wdata", init="x", width=32, pad=False)

    d.posedge(clk)
    e1 = d.posedge(clk)(valid=1, wdata=0x270F)
    d.posedge(clk)(ready=1)
    e3 = d.posedge(clk)(valid=0, wdata="x")
    d.posedge(clk)(ready=0)
    d.arrow(e1.valid, (ready, e3), "hs")

    svg, stats = render_with_stats(d)
    ET.fromstring(svg)
    assert ">0x270F<" in svg
    assert "0x0000270F" not in svg
    assert stats.truncated_labels == []
    assert stats.crowded_regions == []


def test_arrow_avoids_marker_and_note_avoids_grazing_bus_label():
    from waveview import Diagram
    from waveview.layout import compute_layout
    from waveview.svg import render_with_stats

    # 1. Same-row arrow on row 0 dodges marker_labels pill when blocked below by a region.
    d_mkr = Diagram(width=600, end="40ns")
    clk = d_mkr.clock("clk", period="40ns", duty=0.8)
    p1 = d_mkr.point(clk, "5ns")
    p2 = d_mkr.point(clk, "25ns")
    d_mkr.region(clk, p1, p2, "REGION_BLOCKER_TEXT")
    d_mkr.marker("15ns", "M1")
    d_mkr.arrow(p1, p2, "ARR")

    svg_mkr, stats_mkr = render_with_stats(d_mkr)
    root_mkr = ET.fromstring(svg_mkr)
    assert stats_mkr.crowded_arrows == []
    texts_mkr = {el.text: (float(el.attrib["x"]), float(el.attrib["y"])) for el in root_mkr.iter() if el.tag.endswith("text")}
    mkr_x, mkr_y = texts_mkr["M1"]
    arr_x, arr_y = texts_mkr["ARR"]
    # Without marker avoidance, ARR would sit right on M1 at 15ns midpoint; with it, ARR shifts to t=0.35.
    assert abs(arr_y - mkr_y) >= 12.0 or abs(arr_x - mkr_x) >= 30.0

    # 2. Note above wire row moves below the wire instead of grazing an adjacent upper-row bus label (gy=4.3px < 4.8px).
    d_note = Diagram(width=600, end="40ns")
    data = d_note.bus("data", init="x", width=16)
    req = d_note.wire("req", init=0)
    d_note.at("5ns")(data=0xCAFE)
    e_req = d_note.at("10ns")(req=1)
    d_note.at("15ns")(data="x")
    d_note.note(e_req.req, "NOTE_A")

    svg_note, stats_note = render_with_stats(d_note)
    root_note = ET.fromstring(svg_note)
    assert stats_note.crowded_notes == []
    lay = compute_layout(d_note)
    req_mid = lay.by_row[id(req)].mid
    note_y = next(
        float(el.attrib["y"])
        for el in root_note.iter()
        if el.tag.endswith("text") and el.text == "NOTE_A"
    )
    assert note_y > req_mid + 10.0


def test_clock_draws_edge_at_zero_and_gap_end_and_all_gap_width_safe():
    from waveview import Diagram
    from waveview.layout import compute_layout

    # 1.phase=0 clock draws rising edge at t=0 and right at gap.end (30ns).
    d = Diagram(end="50ns")
    clk = d.clock("clk", period="10ns")
    req = d.wire("req", init=0)
    d.at("10ns")(req=1)
    d.gap("20ns")  # [10ns, 30ns)
    d.posedge(clk)(req=0)  # 30ns

    svg = d.to_svg()
    root = ET.fromstring(svg)
    lay = compute_layout(d)
    b_clk = lay.by_row[id(clk)]
    clk_path = next(
        el.attrib["d"]
        for el in root.findall(".//{http://www.w3.org/2000/svg}path")
        if el.attrib.get("stroke") == THEMES[d.theme]["clock"] and el.attrib.get("stroke-width") == "1.5"
    )
    # Must contain vertical rise V<hi> at x(0) and x(30ns) (not starting already high).
    assert clk_path.startswith(f"M{lay.tm.x(lay.tm.visible()[0][0]):g},{b_clk.lo:g}") or (
        f",{b_clk.lo:.0f}" in clk_path.split()[0]
    )
    assert clk_path.count(f"V{b_clk.hi:g}") >= 4  # rises at 0ns, 30ns, 40ns, (50ns)

    # 2. Target width + no clocks + full-span gap must not raise ZeroDivisionError.
    d_full_gap = Diagram(width=900, end="100ns")
    d_full_gap.wire("w", init=0)
    d_full_gap.gap("100ns")
    svg_gap = d_full_gap.to_svg()
    ET.fromstring(svg_gap)


def test_p1_layout_and_svg_collision_guards():
    from waveview import Diagram
    from waveview.layout import PAD, compute_layout, text_width
    from waveview.svg import render_with_stats

    # P1 #10: Note and cross-row arrow avoid measure labels and stay above measure lanes.
    d_meas = Diagram(end="40ns")
    sig = d_meas.wire("sig", init=0)
    e1 = d_meas.at("10ns")(sig=1)
    e2 = d_meas.at("30ns")(sig=0)
    d_meas.measure(e1.sig, e2.sig, "MEAS_SPAN")
    d_meas.note(sig, "20ns", "NOTE_ABOVE_MEAS")

    svg_m, stats_m = render_with_stats(d_meas)
    root_m = ET.fromstring(svg_m)
    lay_m = compute_layout(d_meas)
    assert stats_m.crowded_notes == []
    texts_m = {el.text: float(el.attrib["y"]) for el in root_m.iter() if el.tag.endswith("text")}
    assert texts_m["NOTE_ABOVE_MEAS"] < lay_m.lanes_top
    assert texts_m["NOTE_ABOVE_MEAS"] < texts_m["MEAS_SPAN"] - 12.0

    d_meas_arr = Diagram(end="40ns")
    a = d_meas_arr.wire("a", init=0)
    b = d_meas_arr.wire("b", init=0)
    ea = d_meas_arr.at("5ns")(a=1)
    eb = d_meas_arr.at("35ns")(b=1)
    d_meas_arr.measure(ea.a, eb.b, "MEAS_LANE")
    d_meas_arr.arrow(ea.a, eb.b, "ARR_LANE")
    svg_ma, stats_ma = render_with_stats(d_meas_arr)
    root_ma = ET.fromstring(svg_ma)
    lay_ma = compute_layout(d_meas_arr)
    assert stats_ma.crowded_arrows == []
    arr_y = next(float(el.attrib["y"]) for el in root_ma.iter() if el.tag.endswith("text") and el.text == "ARR_LANE")
    assert arr_y < lay_ma.lanes_top

    # P1 #11: text_width measures spaces in mono=True at 0.61 * size (not 0.34 * size).
    assert text_width("AA BB", size=10, mono=True) > text_width("AABB", size=10, mono=True) + 5.5
    d_mono = Diagram(end="20ns", px_per_ns=5)
    bus = d_mono.bus("data", init="x")
    d_mono.set(data="DE AD BE EF")
    d_mono.at("15ns")(data="x")
    _, stats_mono = render_with_stats(d_mono)
    assert any(item["value"] == "DE AD BE EF" for item in stats_mono.truncated_labels)

    # P1 #12: Row-0 marker keeps its markers_bottom baseline even when a row-0 note adds top padding.
    d_mkr_note = Diagram(end="40ns")
    s_mn = d_mkr_note.wire("sig", init=0)
    d_mkr_note.at("10ns")(sig=1)
    d_mkr_note.at("30ns")(sig=0)
    d_mkr_note.marker("20ns", "MKR_TOP")
    d_mkr_note.note(s_mn, "20ns", "NOTE_ROW0")
    svg_mn, stats_mn = render_with_stats(d_mkr_note)
    root_mn = ET.fromstring(svg_mn)
    lay_mn = compute_layout(d_mkr_note)
    assert stats_mn.crowded_notes == []
    texts_mn = {el.text: float(el.attrib["y"]) for el in root_mn.iter() if el.tag.endswith("text")}
    assert abs(texts_mn["MKR_TOP"] - (lay_mn.markers_bottom - 8.0)) < 1e-6
    assert texts_mn["MKR_TOP"] < texts_mn["NOTE_ROW0"] - 12.0

    # P1 #13: Huge gap tick labels expand step so adjacent axis labels never overlap.
    d_ticks = Diagram(timescale="ns", end="10000100ns")
    d_ticks.wire("sig", init=0)
    d_ticks.gap("10000000ns")
    d_ticks.at("10000020ns")(sig=1)
    svg_t = d_ticks.to_svg()
    root_t = ET.fromstring(svg_t)
    lay_t = compute_layout(d_ticks)
    axis_ticks = sorted(
        (float(el.attrib["x"]), el.text or "")
        for el in root_t.iter()
        if el.tag.endswith("text")
        and abs(float(el.attrib["y"]) - (lay_t.axis_y + 16.0)) < 1e-6
        and el.text
    )
    assert len(axis_ticks) >= 2
    for (x1, lbl1), (x2, lbl2) in zip(axis_ticks, axis_ticks[1:]):
        min_gap = (text_width(lbl1, 10, mono=True) + text_width(lbl2, 10, mono=True)) / 2.0 + 1.0
        assert x2 - x1 >= min_gap

    # P1 #14: Wide centered annotations expand canvas width and respect PAD margins.
    d_wide_ann = Diagram(end="10ns", px_per_ns=5)
    sw = d_wide_ann.wire("s", init=0)
    w1 = d_wide_ann.at("2ns")(s=1)
    w2 = d_wide_ann.at("8ns")(s=0)
    wide_labels = [
        "VERY_LONG_CENTERED_MARKER_LABEL",
        "VERY_LONG_CENTERED_MEASURE_LABEL",
        "VERY_LONG_CENTERED_REGION_LABEL",
        "VERY_LONG_CENTERED_ARROW_LABEL",
    ]
    d_wide_ann.marker("5ns", wide_labels[0])
    d_wide_ann.measure(w1.s, w2.s, wide_labels[1])
    d_wide_ann.region(sw, w1.s, w2.s, wide_labels[2])
    d_wide_ann.arrow(w1.s, w2.s, wide_labels[3])
    svg_wa = d_wide_ann.to_svg()
    root_wa = ET.fromstring(svg_wa)
    lay_wa = compute_layout(d_wide_ann)
    assert abs(float(root_wa.attrib["width"]) - lay_wa.width) < 0.02
    by_lbl = {el.text: float(el.attrib["x"]) for el in root_wa.iter() if el.tag.endswith("text") and el.text}
    for lbl in wide_labels:
        size = 10.0 if "REGION" in lbl else 10.5
        hw = text_width(lbl, size) / 2.0
        cx = by_lbl[lbl]
        assert cx - hw >= PAD - 0.5
        assert cx + hw <= lay_wa.width - PAD + 0.5

    # P1 #16: Default max_width=2400 constrains total SVG width (not just waveform span).
    d_cap = Diagram(end="500ns")
    d_cap.clock("clk", period="10ns")
    d_cap.wire("very_long_peripheral_signal_name", init=0)
    lay_cap = compute_layout(d_cap)
    root_cap = ET.fromstring(d_cap.to_svg())
    assert lay_cap.width <= 2400.0
    assert float(root_cap.attrib["width"]) <= 2400.0


def test_kitty_font_renders_valid_svg_and_inspect_report(tmp_path, monkeypatch):
    from waveview import Diagram
    from waveview.fonts import DEFAULT_MONO, DEFAULT_SANS

    kitty_conf = tmp_path / "kitty.conf"
    kitty_conf.write_text(
        "\n".join(
            [
                "    font_family      Iosevka",
                "    symbol_map U+2E80-U+2EFF,U+3000-U+303F,U+4E00-U+9FFF LXGW WenKai Mono",
                "    symbol_map U+2722 Menlo",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WAVEVIEW_KITTY_CONF", str(kitty_conf))

    d = Diagram(title="Kitty 字体测试")
    clk = d.clock("clk", period="10ns")
    req = d.wire("req", init=0)
    d.posedge(clk)(req=1)

    svg = d.to_svg()
    root = ET.fromstring(svg)
    families = [el.attrib["font-family"] for el in root.iter() if el.tag.endswith("text")]
    assert families
    expected_prefix = "'Iosevka', 'LXGW WenKai Mono', "
    assert any(fam == f"{expected_prefix}{DEFAULT_SANS}" for fam in families)
    assert any(fam == f"{expected_prefix}{DEFAULT_MONO}" for fam in families)
    assert all("LXGW WenKai Mono', 'Menlo'" not in fam for fam in families)

    info = d.to_dict()
    assert info["font"] == {"families": ["Iosevka", "LXGW WenKai Mono"], "source": "kitty"}
    assert "font=Iosevka+LXGW WenKai Mono" in d.inspect("text")

    # XML-special characters in explicit font names stay valid XML.
    d_esc = Diagram(title="Escape", font="Maple Mono & <Demo>")
    d_esc.wire("w", init=0)
    ET.fromstring(d_esc.to_svg())
