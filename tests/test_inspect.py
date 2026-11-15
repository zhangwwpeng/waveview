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
