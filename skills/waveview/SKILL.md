---
name: waveview
description: Use when drawing digital hardware timing diagrams in TUI responses with python_wave fences, verifying SVG layout and timing health before replying, or using Python to inspect and recognize clock-edge timing semantics (sampled vs after register transitions, handshake cycles, and protocol checks).
---

# waveview Skill

Use `waveview` for two tightly connected workflows:
1. **Drawing waveforms in TUI replies** using ```` ```python_wave ```` blocks (rendered inline by `extensions/waveview.ts`).
2. **Self-checking and recognizing timing in Python/CLI** via `waveview check`, `d.sample()`, `d.to_dict()`, and `d.inspect()`—without generating or viewing PNG images.

## Workflow A: TUI 内联画图闭环（先验再发）

When illustrating hardware timing in chat:

1. Write Python code inside a ```` ```python_wave ```` (or ```` ```waveview ````) fence.
2. Assign the `Diagram` to `d` (or another variable—the `omp-waveview` TUI plugin automatically prints `d.to_svg()` or the last `Diagram` instance).
   - **Do NOT** call `print(d.to_svg())` inside the block.
   - **Do NOT** call `d.save(...)` unless the user explicitly asked to write an image file to disk.
3. **Always verify before sending the reply.** The `omp-waveview` TUI renderer errors only rewrite the UI fence and do **not** feed back into your context. Run the global `waveview` CLI on stdin first (assumes `~/.local/bin/waveview` installed via `uv tool install`; inside an uninstalled repo checkout, prefix with `uv run`):

```bash
waveview check - <<'EOF'
# 粘贴即将放进 python_wave 块的完整代码（也支持直接粘贴整段 ```python_wave ... ``` 围栏）
EOF
```

Use `--strict` (`waveview check - --strict`) if you want warnings or layout overlaps to fail with exit code `2`.

### Self-check Checklist (Zero PNG Policy)

Before emitting the ```` ```python_wave ```` block in your response, confirm from `waveview check` output:
- Exit code is `0` (`[waveview check] OK`, `svg=valid`)
- Zero `truncated_labels` (bus labels clipped with `…`)
- Zero `dropped_labels` (bus labels completely hidden because the segment is too narrow)
- Zero `crowded_arrows` (causality arrows still colliding with existing elements)
- Zero `crowded_notes` (single-point callout notes still colliding with existing elements)
- Zero `crowded_regions` (adjacent `region` / `marker` labels colliding above waves)
- The `@posedge` / edge-sampled table and chronological `describe()` steps match the intended hardware protocol cycle-by-cycle

**Never generate `.png` files or call `read_file` on PNGs to inspect your own waveform.** `waveview check` layout stats + clock-edge sampling are complete and unambiguous.

If `truncated_labels`, `dropped_labels`, `crowded_arrows`, `crowded_notes`, or `crowded_regions` appear:
- Pass `max_width=None` on `Diagram(...)` to let long wide-bus diagrams auto-expand past `2400px`, pass `pad=False` on `d.bus(..., width=32, pad=False)` to omit leading zeros without giving up 32-bit range checks, increase `px_per_ns`, or collapse idle intervals with `d.gap(...)`,
- Shorten bus value text or region/arrow/note labels, move long explanations into `d.caption(...)`, or add intermediate cycle spacing.

### Visible Waveform Annotations (Self-Contained Diagrams; Use Library Methods Only)

In TUI replies, `extensions/waveview.ts` replaces the entire ```` ```python_wave ```` block with rendered SVG—any Python `# ...` comment inside the code block becomes **invisible** to the user.

- **Use only the built-in `Diagram` annotation functions; NEVER improvise or hand-draw comments:**
  - Every annotation must go through `d.note(...)`, `d.caption(...)`, `d.arrow(...)`, `d.region(...)`, `d.marker(...)`, or `d.measure(...)`.
  - Do **NOT** write user-facing explanations in Python `#` comments, create fake `d.bus(...)` / `d.wire(...)` rows to hold text, hand-patch `<svg>` elements onto `d.to_svg()`, or fall back to hand-drawn SVG / ASCII diagrams.
- **Put timing explanations directly into the diagram ("能体现在图里就体现在图里"):**
  - Make the waveform figure self-contained so the user can understand the timing just by looking at the diagram.
  - Do **NOT** repeat or dump cycle-by-cycle timing narration separately in the chat reply prose (e.g., "at 10ns `valid` rises, at 20ns `ready` asserts...") whenever that timing can be shown inside the diagram using library annotations—keep surrounding reply text minimal.
- Choose the matching library method for each kind of explanation:
  - `d.note(e.ready, "短标注")`, `d.note(clk.posedge(3), "采样沿")`, or `d.note(ready, e_hs, "握手沿")`: single-point callout badge + leader line attached to a waveform point (no full-height vertical line).
  - `d.arrow(...)`, `d.region(...)`, `d.marker(...)`, `d.measure(...)`: causality arrows accept `Point` (`e1.a`, `clk.posedge(n)`, `sig.at(e2)`) **or `(row, TimeRef)` tuples** such as `d.arrow(e1.valid, (ready, e3), "握手")` when `ready` didn't toggle on that edge; phase/window highlights, global vertical markers, and timing intervals.
  - `d.caption("Note 1: ...", "Note 2: ...")`: left-aligned multi-line explanatory footer below the time axis for protocol rules, preconditions, and longer remarks.

## Workflow B: Python 时序识别与协议核对

Use structured inspection when reading an existing waveform script, verifying RTL-like behavior, or checking clock-domain / handshake timing in Python.

### CLI Commands

```bash
waveview check examples/apb.py               # 健康状态 + 画布指标 + describe() + 时钟采样表
waveview check examples/gated_spi.py --format json   # 结构化 JSON，便于程序读取每拍采样
waveview check examples/apb.py --strict      # 有 warning 或布局拥挤时返回退出码 2
waveview render examples/apb.py -o out/apb.svg       # 显式导出 .svg（-o - 输出 SVG 到 stdout，或 .png --scale 2）
```

### Python Inspection API

```python
samples = d.sample("clk", edge="rise")  # [{"edge": 0, "time": "5ns", "time_s": 5e-9, "signals": {name: {"sampled": ..., "after": ..., "changed": bool}}}, ...]
data = d.to_dict()                      # Full structured dict: ok, svg_valid, canvas, warnings, layout_issues, clock_samples, steps, ...
report = d.inspect(fmt="text")          # fmt="text" or fmt="json"
script = d.describe()                   # Strict time-ordered text playback (old->new)
```

How to interpret `sampled` vs `after` in `d.sample()`:
- **`sampled`**: Signal value seen *immediately before* the clock edge (what flip-flops sample on that edge).
- **`after`**: Signal value settled *at/after* the clock edge (new register outputs triggered on that edge).
- **`changed`**: `True` if `sampled != after` on that edge.

## Core DSL Rules & Common Pitfalls

- **Exact signal names**: `d.wire("valid")` means assignments must use `valid=...` (never `v=...`).
- **Event handle scoping & handshake anchors**: `e = d.posedge(clk)(valid=1)` only exposes signals assigned in that event; accessing `e.ready` raises `WaveError`. When annotating a signal that stays constant on that beat (e.g. a ready/valid handshake), pass `(ready, e3)` directly to `d.arrow(e1.valid, (ready, e3), "握手")`, or pass `e3` as `TimeRef` to `d.note(ready, e3, ...)` / `d.point(ready, e3)` / `ready.at(e3)` instead of hardcoding timestamps. If using `warn_noop=False`, same-value assignments are omitted from `describe()`/`steps` noise while `e.ready` remains valid.
- **Call wait helpers to assign**: `d.posedge(clk)` / `d.delay(...)` / `d.at(...)` return a `Wait` handle — either call it immediately `e = d.posedge(clk)(valid=1)` or follow with `d.set(valid=1)`. Note that any `Event`, `Point`, `Wait`, `Instant`, or `clk.posedge(n)` works directly as `TimeRef` across `arrow`/`note`/`region`/`marker`/`measure`/`point`/`sig.at`. Use `d.clock(..., tco="1.5ns")` (or `d.posedge(clk, tco="1.5ns")`) when showing realistic register clock-to-output propagation delay.
- **Avoid manual nanosecond math for annotations**: Never hardcode `"30ns"` when an `Event`, `Wait`, `d.now`, or `clk.posedge(n)` handle exists for that cycle.
- **Declare then drive**: Initialize idle/reset values in `wire(..., init=...)` or `bus(..., init=...)`; avoid unused signals left at default `"x"` (triggers `WaveWarning`).
- **Self-contained annotations via library methods only**: Put every comment and timing explanation inside the diagram using the provided `Diagram` methods (`d.note`, `d.caption`, `d.arrow`, `d.region`, `d.marker`, `d.measure`)—never improvise Python `#` comments, fake dummy rows, or hand-drawn SVG, and avoid narrating edge-by-edge timing separately in reply prose when the diagram can express it.
- **Out-of-range events**: Events or annotations past manual `end=` emit `WaveWarning`.

## References

See [references/api-and-schema.md](references/api-and-schema.md) for complete `Diagram` / `Clock` / inspection API signatures, `waveview check --format json` schema keys, and copy-ready protocol templates.
