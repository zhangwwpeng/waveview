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
    if os.environ.get("UPDATE_SNAPSHOTS") or not snap.exists():
        SNAPSHOTS.mkdir(exist_ok=True)
        snap.write_text(svg, encoding="utf-8")
    assert svg == snap.read_text(encoding="utf-8"), f"{snap.name} changed; rerun with UPDATE_SNAPSHOTS=1 if intended"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_describe_matches_snapshot(path):
    text = load(path).describe()
    snap = SNAPSHOTS / f"{path.stem}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS") or not snap.exists():
        SNAPSHOTS.mkdir(exist_ok=True)
        snap.write_text(text, encoding="utf-8")
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
    wave_color = THEMES["light"]["wave"]
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
        if el.attrib.get("fill") == THEMES["light"]["row_alt"]
    ]
    assert len(alt_rects) == 1
    assert float(alt_rects[0].attrib["x"]) >= 28.0


def test_sub_millisecond_ticks_format_distinctly():
    from waveview import Diagram

    d = Diagram(timescale="ms", end="2us")
    sig = d.wire("sig", init=0)
    d.at("500ns")(sig=1)
    root = ET.fromstring(d.to_svg())
    muted = THEMES["light"]["muted"]
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
