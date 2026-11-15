"""Tests for AI timing inspection, clock sampling, layout diagnostics, and CLI."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from waveview import Diagram, WaveError
from waveview.cli import main as cli_main
from waveview.svg import render_with_stats


def test_sample_clock_sampled_vs_after_and_negedge():
    d = Diagram()
    clk = d.clock("clk", period="10ns")  # rising edges at 0, 10, 20, ...; falling at 5, 15, 25
    req = d.wire("req", init=0)
    ack = d.wire("ack", init=0)
    data = d.bus("data", init="x", width=8)

    d.posedge(clk)(req=1, data=0xA5)
    d.posedge(clk)(ack=1)
    d.negedge(clk)(data=0x5A)
    d.posedge(clk)(req=0, ack=0)

    rise = d.sample("clk", edge="rise")
    assert [row["time"] for row in rise[:4]] == ["0ns", "10ns", "20ns", "30ns"]
    assert rise[0]["edge"] == 1
    assert rise[0]["signals"]["req"] == {"sampled": "0", "after": "1", "changed": True}
    assert rise[0]["signals"]["data"] == {"sampled": "x", "after": "0xA5", "changed": True}
    assert rise[1]["signals"]["req"] == {"sampled": "1", "after": "1", "changed": False}
    assert rise[1]["signals"]["ack"] == {"sampled": "0", "after": "1", "changed": True}
    # Value changed at falling edge 15ns must appear as already sampled at rising edge 20ns.
    assert rise[2]["signals"]["data"] == {"sampled": "0x5A", "after": "0x5A", "changed": False}
    assert rise[2]["signals"]["req"] == {"sampled": "1", "after": "0", "changed": True}

    fall = d.sample(clk, edge="fall")
    assert fall[0]["time"] == "5ns"
    assert fall[1]["time"] == "15ns"
    assert fall[1]["signals"]["data"] == {"sampled": "0xA5", "after": "0x5A", "changed": True}


def test_sample_clock_respects_gaps_and_gated_windows():
    d = Diagram(end="70ns", warn_noop=False)
    sclk = d.clock("sclk", period="10ns", idle=0, gated=True)
    mosi = d.wire("mosi", init=0)

    sclk.enable("10ns")
    d.posedge(sclk)(mosi=1)  # 10ns (edge #1)
    d.posedge(sclk)(mosi=0)  # 20ns (edge #2)
    d.gap("20ns")            # folds [20ns, 40ns)
    d.posedge(sclk)(mosi=1)  # 40ns -> edge #3? Let's verify exact count vs include_gaps
    sclk.disable("55ns")

    visible_only = d.sample("sclk")
    with_gaps = d.sample("sclk", include_gaps=True)

    # Edges at 10, 20, [30 inside gap], 40, 50.
    assert [r["time"] for r in with_gaps] == ["10ns", "20ns", "30ns", "40ns", "50ns"]
    assert [r["edge"] for r in with_gaps] == [1, 2, 3, 4, 5]
    # Note: gap is half-open [20ns, 40ns), so 20ns (start of gap) is emitted when interval ends at 20ns or starts at 40ns;
    # specifically 30ns lies strictly inside the collapsed gap and must be excluded by default.
    assert "30ns" not in [r["time"] for r in visible_only]
    assert [r["edge"] for r in visible_only] == [1, 2, 4, 5]


def test_sample_clock_invalid_edge():
    d = Diagram()
    d.clock("clk", freq="100MHz")
    with pytest.raises(WaveError, match="edge"):
        d.sample("clk", edge="both")  # type: ignore[arg-type]


def test_render_with_stats_and_to_dict_detects_truncation_drop_and_warnings():
    # Tight horizontal scale so narrow bus labels either truncate or drop.
    d = Diagram(title="Tight Layout", px_per_ns=0.8, end="60ns")
    clk = d.clock("clk", period="10ns")
    bus_a = d.bus("bus_a", init="x")
    unused = d.wire("unused")  # remains init="x" with no changes -> WaveWarning

    d.posedge(clk)(bus_a="VERY_LONG_HEADER_LABEL")
    d.delay("4ns")(bus_a="SHORT_NARROW_SEG_VALUE")
    d.at("50ns")(bus_a="OK")

    svg, stats = render_with_stats(d)
    ET.fromstring(svg)
    assert stats.truncated_labels or stats.dropped_labels

    info = d.to_dict()
    assert info["svg_valid"] is True
    assert info["ok"] is False
    assert any(unused.name in w for w in info["warnings"])
    assert info["layout_issues"]
    assert info["canvas"]["width"] == stats.width
    assert info["canvas"]["height"] == stats.height


def test_inspect_text_and_json_formats():
    d = Diagram(title="Handshake", theme="bamboo")
    clk = d.clock("clk", freq="100MHz")
    valid = d.wire("valid", init=0)
    ready = d.wire("ready", init=0)
    data = d.bus("data", init=0, width=8)

    e1 = d.posedge(clk)(valid=1, data=0xAB)
    e2 = d.posedge(clk)(ready=1)
    d.arrow(e1.valid, e2.ready, "ack")
    d.posedge(clk)(valid=0, ready=0)

    text_report = d.inspect("text")
    assert "[waveview check] OK" in text_report
    assert "theme=bamboo" in text_report
    assert "svg=valid" in text_report
    assert "=== 时序脚本摘要 (describe) ===" in text_report
    assert "=== 时钟上升沿采样 (@posedge) ===" in text_report
    assert "valid=0→1" in text_report
    assert "data=0x00→0xAB" in text_report

    json_report = json.loads(d.inspect("json"))
    assert json_report["ok"] is True
    assert json_report["svg_valid"] is True
    assert json_report["title"] == "Handshake"
    assert json_report["theme"] == "bamboo"
    assert json_report["clocks"][0]["name"] == "clk"
    assert len(json_report["clock_samples"]["clk"]) >= 3
    assert json_report["annotations"][0]["type"] == "arrow"

    with pytest.raises(WaveError, match="inspect"):
        d.inspect("yaml")  # type: ignore[arg-type]


def test_cli_check_file_and_stdin_markdown_fence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    script = tmp_path / "sample.py"
    script.write_text(
        "\n".join(
            [
                "from waveview import Diagram",
                'd = Diagram("CLI Check")',
                'clk = d.clock("clk", freq="100MHz")',
                'v = d.wire("v", init=0)',
                "d.posedge(clk)(v=1)",
                "if __name__ == '__main__':",
                "    raise RuntimeError('should not execute __main__')",
            ]
        ),
        encoding="utf-8",
    )

    rc = cli_main(["check", str(script), "--format", "json", "--strict"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["svg_valid"] is True
    assert out["clock_samples"]["clk"][0]["signals"]["v"] == {
        "sampled": "0",
        "after": "1",
        "changed": True,
    }

    markdown_reply = """说明文字：
```python_wave
from waveview import Diagram
diag = Diagram("Fence One")
clk = diag.clock("clk", period="10ns")
w = diag.wire("w", init=0)
diag.posedge(clk)(w=1)
```
第二个块：
```waveview
from waveview import Diagram
d = Diagram("Fence Two")
clk = d.clock("clk", period="10ns")
w = d.wire("w", init=1)
d.posedge(clk)(w=0)
```
"""
    monkeypatch.setattr("sys.stdin", io.StringIO(markdown_reply))
    rc2 = cli_main(["check", "-", "--format", "json", "--strict"])
    assert rc2 == 0
    multi = json.loads(capsys.readouterr().out)
    assert isinstance(multi, list)
    assert [item["title"] for item in multi] == ["Fence One", "Fence Two"]


def test_cli_check_strict_warnings_errors_and_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    warn_code = """
from waveview import Diagram
d = Diagram(end="10ns")
clk = d.clock("clk", period="10ns")
w = d.wire("w", init=0)
unused = d.wire("unused")  # triggers WaveWarning
d.posedge(clk)(w=1)
"""
    monkeypatch.setattr("sys.stdin", io.StringIO(warn_code))
    assert cli_main(["check", "-"]) == 0
    _ = capsys.readouterr()

    monkeypatch.setattr("sys.stdin", io.StringIO(warn_code))
    assert cli_main(["check", "-", "--strict"]) == 2
    assert "[waveview check] WARN" in capsys.readouterr().out

    # Missing Diagram instance -> exit 1
    monkeypatch.setattr("sys.stdin", io.StringIO("x = 42\n"))
    assert cli_main(["check", "-"]) == 1
    assert "没有定义 Diagram 实例" in capsys.readouterr().err

    # WaveError -> exit 1
    monkeypatch.setattr("sys.stdin", io.StringIO('from waveview import Diagram\nd = Diagram(theme="nope")\n'))
    assert cli_main(["check", "-"]) == 1
    assert "WaveError" in capsys.readouterr().err

    # Missing file -> exit 1
    assert cli_main(["check", str(tmp_path / "does_not_exist.py")]) == 1
    assert "读取失败" in capsys.readouterr().err

    # render -o -> nested SVG output
    clean_py = tmp_path / "clean.py"
    clean_py.write_text(
        'from waveview import Diagram\nd = Diagram("Out")\nclk = d.clock("clk", period="10ns")\nw = d.wire("w", init=0)\nd.posedge(clk)(w=1)\n',
        encoding="utf-8",
    )
    svg_out = tmp_path / "nested" / "dir" / "out.svg"
    assert cli_main(["render", str(clean_py), "-o", str(svg_out)]) == 0
    assert svg_out.is_file()
    ET.fromstring(svg_out.read_text(encoding="utf-8"))


def test_cli_render_stdout_from_fence(monkeypatch: pytest.MonkeyPatch, capsys):
    fenced_with_print = """说明前缀：
```python_wave
from waveview import Diagram
d = Diagram("Stdout Fence")
clk = d.clock("clk", period="10ns")
v = d.wire("v", init=0)
d.posedge(clk)(v=1)
print("noise on stdout")
```
"""
    monkeypatch.setattr("sys.stdin", io.StringIO(fenced_with_print))
    assert cli_main(["render", "-", "-o", "-"]) == 0
    captured = capsys.readouterr()
    assert "noise on stdout" not in captured.out
    assert "已保存" not in captured.out
    assert captured.out.lstrip().startswith("<svg")
    assert captured.out.rstrip().endswith("</svg>")
    ET.fromstring(captured.out)

    # Omitting -o defaults to stdout SVG as well.
    monkeypatch.setattr("sys.stdin", io.StringIO(fenced_with_print))
    assert cli_main(["render", "-"]) == 0
    captured2 = capsys.readouterr()
    assert captured2.out.lstrip().startswith("<svg")
    ET.fromstring(captured2.out)


def test_describe_to_dict_inspect_and_crowded_notes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    d = Diagram(title="Annotated Bus", theme="bamboo")
    clk = d.clock("clk", period="10ns")
    req = d.wire("req", init=0)
    e1 = d.posedge(clk)(req=1)
    d.posedge(clk)(req=0)
    d.note(e1.req, "发起请求")
    d.caption("图注说明一：同步采样。", "图注说明二：单拍有效。")

    desc = d.describe()
    assert '注释：req@0ns "发起请求"' in desc
    assert "图注：图注说明一：同步采样。" in desc
    assert "图注：图注说明二：单拍有效。" in desc

    info = d.to_dict()
    assert info["ok"] is True
    assert info["canvas"]["crowded_notes"] == []
    assert info["annotations"] == [
        {"type": "note", "target": "req@0ns", "row": "req", "time": "0ns", "label": "发起请求"}
    ]
    assert info["captions"] == ["图注说明一：同步采样。", "图注说明二：单拍有效。"]

    text_report = d.inspect("text")
    assert '注释：req@0ns "发起请求"' in text_report
    assert "图注：图注说明二：单拍有效。" in text_report

    # Construct an ultra-dense cluster of 16 notes on the exact same row/time to exhaust candidate offsets.
    dense = Diagram(title="Dense Notes")
    d_clk = dense.clock("clk", period="10ns")
    sig = dense.wire("sig", init=0)
    dense.posedge(d_clk)(sig=1)
    for i in range(16):
        dense.note(sig, "10ns", f"NOTE_{i:02d}")

    dense_info = dense.to_dict()
    assert dense_info["canvas"]["crowded_notes"]
    assert any("注释拥挤：" in issue for issue in dense_info["layout_issues"])
    assert dense_info["ok"] is False

    # Verify CLI `--strict` exits 2 on crowded_notes.
    dense_script = "\n".join(
        [
            "from waveview import Diagram",
            "d = Diagram(end='20ns')",
            "c = d.clock('clk', period='10ns')",
            "s = d.wire('s', init=0)",
            "d.posedge(c)(s=1)",
            *[f"d.note(s, '10ns', 'N{i}')" for i in range(16)],
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(dense_script))
    assert cli_main(["check", "-", "--strict"]) == 2
    assert "注释拥挤：" in capsys.readouterr().out


def test_cli_commonmark_nested_fences_and_render_multiblock_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    nested_markdown = """Tutorial with nested 4-backtick outer fence:

````markdown
Example syntax (must NOT execute):
```python_wave
syntax error ??? !!!
```
````

Live diagram block:

```python_wave
from waveview import Diagram
d = Diagram("Top Level Only")
clk = d.clock("clk", period="10ns", tco="1ns")
q = d.wire("q", init=0)
d.posedge(clk)(q=1)
```
"""
    monkeypatch.setattr("sys.stdin", io.StringIO(nested_markdown))
    assert cli_main(["check", "-", "--format", "json", "--strict"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["title"] == "Top Level Only"
    assert payload["clocks"][0]["tco"] == "1ns"

    multi_blocks = """
```python_wave
from waveview import Diagram
d = Diagram("First")
d.wire("a", init=0)
```
```python_wave
from waveview import Diagram
d = Diagram("Second")
d.wire("b", init=1)
```
"""
    monkeypatch.setattr("sys.stdin", io.StringIO(multi_blocks))
    assert cli_main(["render", "-", "-o", "-"]) == 0
    captured = capsys.readouterr()
    assert "仅输出最后一个" in captured.err
    assert "Second" in captured.out
    ET.fromstring(captured.out)


def test_max_width_none_prevents_wide_bus_truncation():
    def build(max_width):
        d = Diagram(title="Long 32-bit Burst", max_width=max_width)
        clk = d.clock("clk", period="10ns")
        data = d.bus("data", init=0, width=32)
        for i in range(1, 35):
            d.posedge(clk)(data=0x270F + i)
        return d

    capped = build(2400.0).to_dict()
    assert capped["canvas"]["hit_width_cap"] is True
    assert capped["canvas"]["truncated_labels"]
    assert capped["canvas"]["width_drivers"]
    assert capped["canvas"]["width_drivers"][0]["kind"] == "bus"
    assert capped["canvas"]["width_drivers"][0]["signal"] == "data"
    assert any("max_width=None" in issue for issue in capped["layout_issues"])

    uncapped = build(None).to_dict()
    assert uncapped["canvas"]["hit_width_cap"] is False
    assert uncapped["canvas"]["width_drivers"] == []
    assert uncapped["canvas"]["truncated_labels"] == []
    assert uncapped["ok"] is True


def test_crowded_regions_detection_and_cli_strict(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    d = Diagram(title="Crowded Regions", px_per_ns=2.0, end="40ns")
    d.clock("clk", period="10ns")
    wdata = d.bus("wdata", init=0, width=16, pad=False)
    d.at("10ns")(wdata=0x10)
    d.at("20ns")(wdata=0x20)
    d.region(wdata, "0ns", "10ns", "burst_one")
    d.region(wdata, "10ns", "20ns", "burst_two")

    info = d.to_dict()
    assert info["canvas"]["crowded_regions"]
    assert any("区域拥挤：" in issue for issue in info["layout_issues"])
    assert info["ok"] is False

    script = "\n".join(
        [
            "from waveview import Diagram",
            "d = Diagram(px_per_ns=2.0, end='40ns')",
            "d.clock('clk', period='10ns')",
            "wdata = d.bus('wdata', init=0, width=16, pad=False)",
            "d.at('10ns')(wdata=0x10)",
            "d.at('20ns')(wdata=0x20)",
            "d.region(wdata, '0ns', '10ns', 'burst_one')",
            "d.region(wdata, '10ns', '20ns', 'burst_two')",
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(script))
    assert cli_main(["check", "-", "--strict"]) == 2
    assert "区域拥挤：" in capsys.readouterr().out


def test_handshakes_expect_handshake_and_expect_stable():
    d = Diagram("Protocol Assertions")
    clk = d.clock("clk", period="10ns")
    valid = d.wire("valid", init=0)
    ready = d.wire("ready", init=0)
    wdata = d.bus("wdata", init="x", width=32, pad=False)

    d.posedge(clk)                                   # #1 0ns
    e_drive = d.posedge(clk)(valid=1, wdata=0x270F)  # #2 10ns
    e_early = d.posedge(clk)(ready=1)                # #3 20ns (sampled valid=1, ready=0)
    e_hs1 = d.posedge(clk)(wdata=0x2710)             # #4 30ns (sampled valid=1, ready=1 -> hs #1)
    e_hs2 = d.posedge(clk)(valid=0, ready=0, wdata="x")  # #5 40ns (sampled valid=1, ready=1 -> hs #2)

    hs = d.handshakes(clk, valid, ready)
    assert [(h["edge"], h["time"]) for h in hs] == [(4, "30ns"), (5, "40ns")]
    assert d.expect_handshake(clk, valid, "ready", at=e_hs1, count=2) == hs
    assert d.expect_handshake("clk", "valid", ready, at=e_hs2) == hs

    # Off-by-one beat fails with helpful sampled + "在该沿才刚变为" + actual beats hints.
    with pytest.raises(WaveError, match=r"在该沿才刚变为.*实际发生握手的边沿.*#4\(30ns\)"):
        d.expect_handshake(clk, valid, ready, at=e_early)

    with pytest.raises(WaveError, match="期望.*发生 1 次握手，实际发生 2 次"):
        d.expect_handshake(clk, valid, ready, count=1)

    with pytest.raises(WaveError, match="有效上升沿"):
        d.expect_handshake(clk, valid, ready, at="25ns")

    # expect_stable allows change right at window end (40ns), rejects change strictly inside [10ns, 40ns).
    assert d.expect_stable(wdata, e_drive, e_hs1) == 0x270F
    with pytest.raises(WaveError, match="发生了跳变：30ns 处 0x270F→0x2710"):
        d.expect_stable(wdata, e_drive, e_hs2)
    with pytest.raises(WaveError, match='为 "x"'):
        d.expect_stable(wdata, "0ns", e_drive)

    # Also verify tco-shifted event handle still resolves cleanly back to its driving clock edge.
    d_tco = Diagram("TCO Assertions")
    c_tco = d_tco.clock("clk", period="10ns", tco="1.5ns")
    v_tco = d_tco.wire("valid", init=0)
    r_tco = d_tco.wire("ready", init=0)
    e_drive_tco = d_tco.posedge(c_tco)(valid=1)                 # #1 1.5ns (clock edge @0ns)
    e_early_tco = d_tco.posedge(c_tco, tco="2.5ns")(ready=1)    # #2 12.5ns (clock edge @10ns)
    e_hs_tco = d_tco.posedge(c_tco)(valid=0)                    # #3 21.5ns (clock edge @20ns)
    samples_tco = d_tco.sample(c_tco)
    assert samples_tco[0]["signals"]["valid"] == {"sampled": "0", "after": "1", "changed": True}
    assert samples_tco[1]["signals"]["ready"] == {"sampled": "0", "after": "1", "changed": True}
    assert samples_tco[2]["signals"]["valid"] == {"sampled": "1", "after": "0", "changed": True}
    assert d_tco.expect_handshake(c_tco, v_tco, r_tco, at=e_hs_tco, count=1) == [
        {"edge": 3, "time": "20ns", "time_s": 2e-08}
    ]
    # Passing an event from a one-off `tco="2.5ns"` wait resolves back to clock edge #2 and gives 1-cycle hint.
    with pytest.raises(WaveError, match=r"在该沿才刚变为.*#3\(20ns\)"):
        d_tco.expect_handshake(c_tco, v_tco, r_tco, at=e_early_tco)


def test_cli_handles_system_exit_as_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    bad_zero = "\n".join(
        [
            "import sys",
            "from waveview import Diagram",
            "d = Diagram()",
            "d.clock('clk', period='10ns')",
            "sys.exit(0)",
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(bad_zero))
    assert cli_main(["check", "-"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SystemExit" in captured.err

    bad_three = "\n".join(
        [
            "import sys",
            "sys.exit(3)",
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(bad_three))
    assert cli_main(["render", "-", "-o", str(tmp_path / "out.svg")]) == 1
    assert "SystemExit" in capsys.readouterr().err

    # Multi-block input still reports subsequent blocks even when block 1 calls sys.exit.
    multi = "\n".join(
        [
            "```python_wave",
            "import sys",
            "sys.exit(0)",
            "```",
            "",
            "```python_wave",
            "from waveview import Diagram",
            "d = Diagram(title='Second Block')",
            "c = d.clock('clk', period='10ns')",
            "req = d.wire('req', init=0)",
            "d.posedge(c)(req=1)",
            "```",
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(multi))
    assert cli_main(["check", "-", "--format", "json"]) == 1
    multi_cap = capsys.readouterr()
    assert "SystemExit" in multi_cap.err
    assert "Second Block" in multi_cap.out


def test_dropped_bus_label_inside_gap_reported_in_inspect_and_stats():
    import warnings

    from waveview import Diagram
    from waveview.inspect import to_dict
    from waveview.svg import render_with_stats

    d = Diagram(end="50ns")
    d.bus("data", init="x")
    d.at("12ns")(data=0xBEEF)
    d.at("28ns")(data="x")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        d.at("10ns")
    d.gap("20ns")  # hides [10ns, 30ns), completely swallowing 0xBEEF

    _, stats = render_with_stats(d)
    assert any(item["signal"] == "data" and item.get("reason") == "inside_gap" for item in stats.dropped_labels)

    report = to_dict(d)
    assert report["ok"] is False
    assert any("完全落在 gap 省略区间内未能显示" in issue for issue in report["layout_issues"])


def test_handshakes_skip_gaps_by_default_and_cli_p2_p3(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path):
    from waveview import Diagram
    from waveview.cli import _extract_blocks, main as cli_main
    from waveview.errors import WaveError

    # 1. handshakes() and expect_handshake() default include_gaps=False consistently with sample().
    d = Diagram(end="50ns")
    clk = d.clock("clk", period="10ns")
    v = d.wire("valid", init=0)
    r = d.wire("ready", init=0)
    d.posedge(clk)(valid=1, ready=1)     # #1 0ns drives high
    e_in_gap = d.posedge(clk)            # #2 10ns: edge INSIDE gap below (wait: [15ns, 35ns) hides 20ns & 30ns!)
    d.at("15ns")
    d.gap("20ns")                        # hides [15ns, 35ns) -> edge #3(20ns), #4(30ns)
    e_vis = d.posedge(clk)               # #5 40ns (visible handshake)
    assert [(h["edge"], h["time"]) for h in d.handshakes(clk, v, r)] == [(2, "10ns"), (5, "40ns")]
    assert len(d.handshakes(clk, v, r, include_gaps=True)) == 4
    d.expect_handshake(clk, v, r, at=e_vis, count=2)
    with pytest.raises(WaveError, match="gap 省略区间内"):
        d.expect_handshake(clk, v, r, at=clk.posedge(3))
    assert len(d.expect_handshake(clk, v, r, at=clk.posedge(3), include_gaps=True)) == 4

    # 2. _extract_blocks handles .py docstring fences, prose + python fence, tilde/indented fences, and empty .md.
    py_with_doc = '"""Doc:\n```python_wave\nbroken()\n```\n"""\nd = Diagram()\n'
    assert _extract_blocks(py_with_doc, "demo.py") == [("demo.py", py_with_doc)]

    prose_then_py = "说明文本\n\n```python\nd = Diagram()\n```\n"
    assert _extract_blocks(prose_then_py, "<stdin>") == [("<stdin>", "d = Diagram()\n")]

    indented_tilde = "- bullet\n  ~~~python_wave\n  from waveview import Diagram\n  d = Diagram()\n  ~~~\n"
    blocks = _extract_blocks(indented_tilde, "notes.md")
    assert blocks == [("notes.md", "from waveview import Diagram\nd = Diagram()\n")]

    with pytest.raises(WaveError, match="没有找到"):
        _extract_blocks("# Just a title\n", "empty.md")

    # 3. CLI selects last instantiated Diagram regardless of global insertion order.
    order_script = "\n".join(
        [
            "from waveview import Diagram",
            "x = None",
            "y = Diagram(title='Second Created')",
            "x = Diagram(title='First Variable Reassigned Last')",
            "c = x.clock('clk', period='10ns'); w = x.wire('w', init=0); x.posedge(c)(w=1)",
            "c2 = y.clock('clk', period='10ns'); w2 = y.wire('w', init=0); y.posedge(c2)(w=1)",
        ]
    )
    # Wait: x was created after y even though key 'x' came first in globals!
    monkeypatch.setattr("sys.stdin", io.StringIO(order_script))
    assert cli_main(["check", "-", "--format", "json"]) == 0
    out_text = capsys.readouterr().out
    assert "First Variable Reassigned Last" in out_text

    # 4. check reports subsequent valid blocks even when block 1 raises WaveError/SyntaxError.
    multi_err = "\n".join(
        [
            "```python_wave",
            "d = (  # intentional syntax error",
            "```",
            "",
            "```python_wave",
            "from waveview import Diagram",
            "d = Diagram(title='Block Two Recovered')",
            "c = d.clock('clk', period='10ns')",
            "req = d.wire('req', init=0)",
            "d.posedge(c)(req=1)",
            "```",
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(multi_err))
    assert cli_main(["check", "-", "--format", "json"]) == 1
    err_cap = capsys.readouterr()
    assert "SyntaxError" in err_cap.err
    assert "Block Two Recovered" in err_cap.out


def test_target_width_and_width_drivers():
    # 1. Clean fit to target width on standard clocked diagram.
    d = Diagram("AXI Target Width", width=900)
    clk = d.clock("clk", freq="100MHz")
    valid = d.wire("valid", init=0)
    data = d.bus("data", init="x", width=16, pad=False)
    d.posedge(clk)
    d.posedge(clk)(valid=1, data=0x270F)
    d.posedge(clk)(valid=0, data="x")

    info = d.to_dict()
    assert info["ok"] is True
    assert info["canvas"]["width"] == 900.0
    assert info["canvas"]["target_width"] == 900.0
    assert info["canvas"]["width_drivers"] == []
    assert info["canvas"]["truncated_labels"] == []

    # 2. Target width accounts accurately for grouped label rail + collapsed gaps.
    d_gap = Diagram("Gapped Target", width=850)
    c_gap = d_gap.clock("aclk", freq="100MHz")
    with d_gap.group("Write Channel"):
        awvalid = d_gap.wire("awvalid", init=0, label="AWVALID")
    d_gap.posedge(c_gap)(awvalid=1)
    d_gap.gap("50ns")
    d_gap.posedge(c_gap)(awvalid=0)

    gap_info = d_gap.to_dict()
    assert abs(gap_info["canvas"]["width"] - 850.0) <= 0.05
    assert gap_info["canvas"]["width_drivers"] == []

    # 3. Bus hard non-truncation constraint wins over narrow width= and reports driver.
    d_bus = Diagram("Bus Driver", width=360)
    c_bus = d_bus.clock("clk", freq="100MHz")
    wdata = d_bus.bus("wdata", init=0, width=32, pad=False)
    d_bus.posedge(c_bus)(wdata=0xCAFEBABE)
    d_bus.posedge(c_bus)(wdata=0)

    bus_info = d_bus.to_dict()
    assert bus_info["ok"] is True
    assert bus_info["canvas"]["truncated_labels"] == []
    assert bus_info["canvas"]["width"] > 360.0
    assert any(
        drv["kind"] == "bus" and drv["signal"] == "wdata" and drv["value"] == "0xCAFEBABE"
        for drv in bus_info["canvas"]["width_drivers"]
    )
    text_out = d_bus.inspect("text")
    assert '宽度自适应：目标 360px -> 实际 ' in text_out
    assert 'wdata="0xCAFEBABE"' in text_out

    # 4. Clock floor (5px per half-cycle) wins over narrow width= on many-cycle trace.
    d_clk = Diagram("Clock Floor", width=300, end="500ns")
    d_clk.clock("clk", period="10ns")
    sig = d_clk.wire("sig", init=0)
    d_clk.at("20ns")(sig=1)

    clk_info = d_clk.to_dict()
    assert clk_info["canvas"]["width"] > 300.0
    assert any(drv["kind"] == "clock_floor" for drv in clk_info["canvas"]["width_drivers"])

    # 5. Title and caption exceeding target width are recorded in width_drivers.
    d_cap = Diagram("X" * 80, width=400)
    c_cap = d_cap.clock("clk", period="10ns")
    v_cap = d_cap.wire("v", init=0)
    d_cap.posedge(c_cap)(v=1)
    d_cap.caption("Very long explanatory caption footer " * 4)

    cap_info = d_cap.to_dict()
    assert cap_info["canvas"]["width"] > 400.0
    kinds = {drv["kind"] for drv in cap_info["canvas"]["width_drivers"]}
    assert "caption" in kinds
    assert "title" in kinds
