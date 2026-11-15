"""Command-line interface for checking, inspecting, and rendering diagrams."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import traceback
import warnings
from pathlib import Path
from typing import Sequence

from .diagram import Diagram
from .errors import WaveError, WaveWarning
from .inspect import inspect_diagram, to_dict

_OPEN_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _scan_top_level_fences(raw: str) -> list[tuple[str, str, str]]:
    """Return ``(fence_char, info_head, body)`` for closed top-level Markdown fences."""
    lines = raw.splitlines(keepends=True)
    fences: list[tuple[str, str, str]] = []
    i = 0
    while i < len(lines):
        clean = lines[i].rstrip("\r\n")
        m = _OPEN_FENCE_RE.match(clean)
        if not m:
            i += 1
            continue
        fence = m.group(1)
        fence_char = fence[0]
        fence_len = len(fence)
        info = m.group(2).strip()
        if fence_char == "`" and "`" in info:
            i += 1
            continue
        close_re = re.compile(rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ \t]*$")
        close_idx = -1
        for j in range(i + 1, len(lines)):
            if close_re.match(lines[j].rstrip("\r\n")):
                close_idx = j
                break
        if close_idx < 0:
            i += 1
            continue
        head = info.split()[0] if info else ""
        body = "".join(lines[i + 1 : close_idx])
        fences.append((fence_char, head, body))
        i = close_idx + 1
    return fences


def _extract_blocks(raw: str, filename: str) -> list[tuple[str, str]]:
    fences = _scan_top_level_fences(raw)
    wave_blocks = [
        body for char, head, body in fences if char == "`" and head in ("python_wave", "waveview")
    ]
    if wave_blocks:
        if len(wave_blocks) == 1:
            return [(filename, wave_blocks[0])]
        return [(f"{filename}#block-{i + 1}", b) for i, b in enumerate(wave_blocks)]
    if raw.lstrip().startswith(("```", "~~~")):
        py_blocks = [body for _, head, body in fences if head in ("python", "py")]
        if py_blocks:
            if len(py_blocks) == 1:
                return [(filename, py_blocks[0])]
            return [(f"{filename}#block-{i + 1}", b) for i, b in enumerate(py_blocks)]
    return [(filename, raw)]


def _read_source(source: str) -> tuple[str, str]:
    if source == "-":
        return "<stdin>", sys.stdin.read()
    path = Path(source)
    return str(path), path.read_text(encoding="utf-8")


def _run_diagram_code(code: str, filename: str) -> tuple[Diagram, list[str]]:
    ns: dict[str, object] = {"__name__": "__waveview_cli__"}
    buf = io.StringIO()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", WaveWarning)
        with contextlib.redirect_stdout(buf):
            exec(compile(code, filename, "exec"), ns)

    exec_warnings = [str(w.message) for w in caught]
    d = ns.get("d")
    if isinstance(d, Diagram):
        return d, exec_warnings
    found = [v for v in ns.values() if isinstance(v, Diagram)]
    if found:
        return found[-1], exec_warnings
    raise WaveError(f"{filename} 没有定义 Diagram 实例（请先写 d = Diagram(...)）")


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        filename, raw = _read_source(args.source)
    except OSError as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 1

    blocks = _extract_blocks(raw, filename)
    reports = []
    any_issue = False
    any_error = False

    for label, code in blocks:
        try:
            d, exec_warnings = _run_diagram_code(code, label)
            info = to_dict(d, extra_warnings=exec_warnings)
            if not info["svg_valid"]:
                any_error = True
            if not info["ok"]:
                any_issue = True
            reports.append((label, d, exec_warnings, info))
        except Exception as exc:
            any_error = True
            if isinstance(exc, (WaveError, SyntaxError)):
                print(f"[{label}] {type(exc).__name__}: {exc}", file=sys.stderr)
            else:
                traceback.print_exc(file=sys.stderr)
            return 1

    if args.format == "json":
        payload = reports[0][3] if len(reports) == 1 else [r[3] for r in reports]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        chunks = [inspect_diagram(d, fmt="text", extra_warnings=w) for _, d, w, _ in reports]
        print("\n\n".join(chunks))

    if any_error:
        return 1
    if args.strict and any_issue:
        return 2
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    try:
        filename, raw = _read_source(args.source)
        blocks = _extract_blocks(raw, filename)
        if len(blocks) > 1:
            print(
                f"警告: 输入包含 {len(blocks)} 个代码块，render 仅输出最后一个（{blocks[-1][0]}）",
                file=sys.stderr,
            )
        d, exec_warnings = _run_diagram_code(blocks[-1][1], blocks[-1][0])
        for w in exec_warnings:
            print(f"警告: {w}", file=sys.stderr)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", WaveWarning)
            if args.output == "-":
                svg = d.to_svg()
            else:
                d.save(args.output, scale=args.scale)
                svg = ""
        for cw in caught:
            print(f"警告: {cw.message}", file=sys.stderr)
        if args.output == "-":
            sys.stdout.write(svg if svg.endswith("\n") else svg + "\n")
        else:
            print(f"已保存：{args.output}")
        return 0
    except Exception as exc:
        if isinstance(exc, (WaveError, SyntaxError, OSError)):
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        else:
            traceback.print_exc(file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waveview",
        description="检查、采样分析与渲染 waveview 时序图脚本",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser(
        "check",
        help="验证渲染与排版并输出结构化时序报告（支持 .py、stdin 或 ```python_wave 围栏文本）",
    )
    p_check.add_argument("source", nargs="?", default="-", help="脚本路径，或 '-' 从标准输入读取")
    p_check.add_argument("-f", "--format", choices=("text", "json"), default="text", help="输出格式")
    p_check.add_argument("--strict", action="store_true", help="出现任何告警或排版问题时返回非 0 退出码")
    p_check.set_defaults(func=_cmd_check)

    p_render = sub.add_parser("render", help="运行脚本并输出 SVG 或 PNG")
    p_render.add_argument("source", nargs="?", default="-", help="脚本路径，或 '-' 从标准输入读取")
    p_render.add_argument(
        "-o",
        "--output",
        default="-",
        help="输出路径（.svg 或 .png；'-' 或省略时输出 SVG 到标准输出）",
    )
    p_render.add_argument("--scale", type=float, default=2.0, help="PNG 缩放倍数（默认 2）")
    p_render.set_defaults(func=_cmd_render)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
