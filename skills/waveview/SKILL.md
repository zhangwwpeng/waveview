---
name: waveview
description: Use when drawing digital hardware timing diagrams in TUI responses with python_wave fences, verifying SVG layout and timing health before replying, or using Python to inspect and recognize clock-edge timing semantics (sampled vs after register transitions, handshake cycles, and protocol checks).
---

# waveview Skill

Use `waveview` for two tightly connected workflows:
1. **Drawing waveforms in TUI replies** using ```` ```python_wave ```` blocks (rendered inline by `plugins/omp-waveview`).
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
- The `@posedge` / edge-sampled table and chronological `describe()` steps match the intended hardware protocol cycle-by-cycle

**Never generate `.png` files or call `read_file` on PNGs to inspect your own waveform.** `waveview check` layout stats + clock-edge sampling are complete and unambiguous.

If `truncated_labels`, `dropped_labels`, or `crowded_arrows` appear:
- Increase `px_per_ns` (or shorten `end` / collapse idle intervals with `d.gap(...)`),
- Shorten bus value text (e.g., `D0` instead of `0xDEAD_BEEF`), or
- Shorten or reposition arrow labels / add intermediate cycle spacing.

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
- **Event handle scoping**: `e = d.posedge(clk)(valid=1)` (or `d.set(...)`) only exposes signals assigned in that event; accessing `e.ready` raises `WaveError`.
- **Call wait helpers to assign**: `d.posedge(clk)` / `d.delay(...)` / `d.at(...)` return a `Wait` handle — either call it immediately `e = d.posedge(clk)(valid=1)` or follow with `d.set(valid=1)`.
- **Declare then drive**: Initialize idle/reset values in `wire(..., init=...)` or `bus(..., init=...)`; avoid unused signals left at default `"x"` (triggers `WaveWarning`).
- **Out-of-range events**: Events or annotations past manual `end=` emit `WaveWarning`.

## References

See [references/api-and-schema.md](references/api-and-schema.md) for complete `Diagram` / `Clock` / inspection API signatures, `waveview check --format json` schema keys, and copy-ready protocol templates.
