"""Tests for extensions/waveview.ts Markdown fence scanning & sandboxed child execution."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from waveview.cli import _extract_blocks

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extensions" / "waveview.ts"


def _find_node() -> str | None:
    node = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)
    if not node:
        return None
    ver = subprocess.run([node, "-v"], capture_output=True, text=True, check=False)
    if ver.returncode != 0:
        return None
    parts = ver.stdout.strip().lstrip("v").split(".")
    major, minor = int(parts[0]), int(parts[1])
    if (major, minor) < (22, 6):
        return None
    return node


def _run_node(script: str, extra_env: dict[str, str] | None = None) -> dict:
    node = _find_node()
    if node is None:
        pytest.skip("Node >= 22.6 required to execute extensions/waveview.ts directly")
    env = os.environ.copy()
    env["WAVEVIEW_ROOT"] = str(ROOT)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [node, "--no-warnings", "--input-type=module", "-e", script],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"node failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return json.loads(proc.stdout)


def test_waveview_ts_fences_and_parity():
    node_code = f"""
import * as ext from {json.dumps(EXT.as_uri())};

const calls = [];
process.env.WAVEVIEW_BIN = "/nonexistent/fake_bin";

// Test replaceWaveFences fence handling + disable switch using a fake local CLI via python script in PATH/WAVEVIEW_BIN:
const md = [
  "Intro text",
  "",
  "````markdown",
  "```python_wave",
  "from waveview import Diagram",
  "d = Diagram()",
  "```",
  "````",
  "",
  "- item",
  "  ~~~python_wave",
  "  from waveview import Diagram",
  "  d = Diagram(title='Indented Tilde')",
  "  clk = d.clock('clk', period='10ns')",
  "  req = d.wire('req', init=0)",
  "  d.posedge(clk)(req=1)",
  "  ~~~",
].join("\\n") + "\\n";

process.env.WAVEVIEW_DISABLE = "1";
const disabledOut = ext.replaceWaveFences(md);
delete process.env.WAVEVIEW_DISABLE;

// Use actual CLI via uv or .venv by clearing fake WAVEVIEW_BIN:
delete process.env.WAVEVIEW_BIN;
ext.clearWaveCache();
const enabledOut = ext.replaceWaveFences(md);
const dedented = ext.dedentWaveCode("  a = 1\\n    b = 2\\n");

console.log(JSON.stringify({{
  disabledSame: disabledOut === md,
  enabledOut,
  dedented,
}}));
"""
    res = _run_node(node_code)
    assert res["disabledSame"] is True
    assert res["dedented"] == "a = 1\n  b = 2\n"
    out = res["enabledOut"]
    # Outer 4-backtick block kept verbatim (nested fence NOT rendered):
    assert "````markdown\n```python_wave" in out
    # Indented tilde block rendered to SVG fence (not error/raw):
    assert "```svg\n<svg" in out

    # Parity check with cli._extract_blocks on the same Markdown:
    md_for_cli = (
        "Intro text\n\n"
        "````markdown\n```python_wave\nbad\n```\n````\n\n"
        "- item\n  ~~~python_wave\n  d = Diagram()\n  ~~~\n"
    )
    assert _extract_blocks(md_for_cli, "test.md") == [("test.md", "d = Diagram()\n")]


def test_waveview_ts_error_recovery_timeout_and_isolation(tmp_path: Path):
    fake_secret = "SUPER_SECRET_TOKEN_XYZ"
    node_code = f"""
import * as ext from {json.dumps(EXT.as_uri())};

// 1. Missing executable reports clean error and recovers immediately when WAVEVIEW_BIN is corrected (no permanent failure lock).
ext.clearWaveCache();
process.env.WAVEVIEW_BIN = "/nonexistent/no_such_waveview_cli";
const missingOut = ext.renderWaveBlock("from waveview import Diagram\\nd = Diagram()\\n");
delete process.env.WAVEVIEW_BIN;
const recoveredOut = ext.renderWaveBlock("from waveview import Diagram\\nd = Diagram(title='Recovered')\\nc = d.clock('c', period='10ns')\\nw = d.wire('w', init=0)\\nd.posedge(c)(w=1)\\n");

// 2. Timeout produces human-readable timeout message instead of "exit null".
ext.clearWaveCache();
process.env.WAVEVIEW_TIMEOUT_MS = "100";
const timeoutOut = ext.renderWaveBlock("while True:\\n    pass\\n");
delete process.env.WAVEVIEW_TIMEOUT_MS;

// 3. Child runs in tmpdir and strips secret env vars (prints them into ValueError message via stderr).
ext.clearWaveCache();
process.env.OPENAI_API_KEY = {json.dumps(fake_secret)};
const probeCode = [
  "import os",
  "raise ValueError('PROBE:' + os.getcwd() + '|SECRET=' + repr(os.environ.get('OPENAI_API_KEY')))",
].join("\\n");
const probeOut = ext.renderWaveBlock(probeCode);

console.log(JSON.stringify({{
  missingOut,
  recoveredOut,
  timeoutOut,
  probeOut,
}}));
"""
    res = _run_node(node_code)
    assert "未找到可执行文件" in res["missingOut"]
    assert res["recoveredOut"].startswith("```svg\n<svg")
    assert "执行超时" in res["timeoutOut"]
    assert "exit null" not in res["timeoutOut"]
    assert fake_secret not in res["probeOut"]
    assert "SECRET=None" in res["probeOut"]
    expected_tmp = os.path.realpath(tempfile.gettempdir())
    # Child cwd inside tmpdir should match resolved temp dir prefix:
    assert f"PROBE:{expected_tmp}" in res["probeOut"] or f"PROBE:{tempfile.gettempdir().rstrip('/')}" in res["probeOut"]


def test_waveview_ts_inherits_kitty_font_conf(tmp_path: Path):
    kitty_conf = tmp_path / "kitty.conf"
    kitty_conf.write_text(
        "font_family Iosevka\nsymbol_map U+4E00-U+9FFF LXGW WenKai Mono\n",
        encoding="utf-8",
    )
    node_code = f"""
import * as ext from {json.dumps(EXT.as_uri())};

ext.clearWaveCache();
process.env.WAVEVIEW_KITTY_CONF = {json.dumps(str(kitty_conf))};
const out = ext.renderWaveBlock([
  "from waveview import Diagram",
  "d = Diagram(title='Kitty Font')",
  "c = d.clock('clk', period='10ns')",
  "w = d.wire('w', init=0)",
  "d.posedge(c)(w=1)",
].join("\\n"));
console.log(JSON.stringify({{ out }}));
"""
    res = _run_node(node_code)
    out = res["out"]
    assert out.startswith("```svg\n<svg")
    assert "'Iosevka', 'LXGW WenKai Mono', " in out
