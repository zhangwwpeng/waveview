---
name: waveview
description: Use when drawing digital hardware timing diagrams in TUI responses with python_wave fences, verifying SVG layout and timing health before replying, or using Python to inspect and recognize clock-edge timing semantics (sampled vs after register transitions, handshake cycles, and protocol checks).
---

# waveview Skill

Use `waveview` for TUI waveform diagrams (```` ```python_wave ```` blocks rendered inline by `extensions/waveview.ts`) and headless layout/timing checks (`waveview check`)—without generating or viewing PNG images.

## Quick API Cheat Sheet

```python
d = Diagram(title="...", theme="bamboo", width=900)  # theme: light|dark|bamboo; width targets total px (mutually exclusive with px_per_ns); max_width=2400.0 (None=uncapped); warn_noop=True
clk = d.clock("clk", freq="100MHz", phase=0, duty=0.5, tco=0)  # or period="10ns"; gated=True, idle=0|1
valid = d.wire("valid", init=0, label="VALID")                 # values: 0, 1, "x", "z"
data = d.bus("data", init="x", width=32, fmt="hex", pad=False) # fmt: hex|dec|bin; pad=False omits leading 0s
with d.group("Channel"): ...                                   # nested left-rail group brackets

e1 = d.posedge(clk, n=1)(valid=1, data=0x270F)  # also negedge/delay/at/gap + d.set(...)
d.arrow(e1.valid, (ready, e2), "handshake")     # src/dst: Point, clk.posedge(n), sig.at(t), d.point(row, ref), or (row, TimeRef)
d.region(data, e1, e2, "burst"); d.marker(e2, "hs"); d.measure(e1, e2, "latency")
d.note(clk, e2, "hs beat"); d.caption("Note 1: ...", "Note 2: ...")  # self-contained text footer

d.expect_handshake(clk, valid, ready, at=e2, count=1)  # asserts sampled valid==1 && ready==1 on visible edge (include_gaps=False)
d.expect_stable(data, e1, e2, no_x=True)               # asserts unchanged & non-x/z over [start, end)
```

## Core Timing & Semantics (Read Carefully)

1. **`phase=0` first edge & cursor (`d.now`)**:
   - `d.clock(freq="100MHz")` (`period="10ns"`, `phase=0`, `duty=0.5`) places rising edges at **`0ns (#1), 10ns (#2), 20ns (#3), ...`** (NOT `5 / 15 / 25ns`) and falling edges at `5ns, 15ns, ...`.
   - `d.now` starts as `None` (before `t=0`), so the **first `d.posedge(clk)` lands on `#1` at `0ns`**. Call a bare `d.posedge(clk)` first if `[0, period)` should hold initial idle values before driving `#2`.
2. **One RTL rule — `#k` assignment (`after`) vs `#(k+1)` capture (`sampled`)**:
   - Every edge `t` in `d.sample(clk)` has **`sampled`** (`value_before(t)`: what flip-flops capture *just before* the edge) and **`after`** (`value_at(t)`: what synchronous assignments produce *at/after* the edge, shifted by `tco` if set); `changed` is `sampled != after`.
   - Therefore, **`e = d.posedge(clk)(valid=1)` on edge `#k` updates `#k.after`, and is first seen in `sampled` on edge `#(k+1)`**. Handshakes occur at `e_hs` when both `valid` and `ready` are `1` in `#e_hs.sampled`—use `d.expect_handshake(...)` & `d.expect_stable(...)` when verifying protocol timing so off-by-one beats fail immediately.
3. **Canvas width & `px_per_ns`**:
   - Pass **`Diagram(..., width=900)`** when you want a generous target canvas width (~850–950px) in a single pass instead of guessing `px_per_ns`. Hard constraints (clock `5px/half` readability floor, bus value fit without truncation, long `caption`/`title`/`note`/`marker`) may still widen the diagram beyond `width`, in which case `canvas.width_drivers` tells you exactly which bus segment or annotation drove the width.
   - Reserve `px_per_ns` for explicit zoom factors; `width` and `px_per_ns` are mutually exclusive (`WaveError`).
4. **Event handles & anchors**:
   - `e = d.posedge(clk)(valid=1)` only exposes signals assigned in *that* event (`e.ready` raises `WaveError`). Every `Event`, `Point`, `Wait`, `Instant`, or `clk.posedge(n)` works directly as `TimeRef` in `d.at`, `arrow`, `note`, `region`, `marker`, `measure`, `point`, and `(row, TimeRef)` tuples—never hardcode `"30ns"` when a handle exists.
   - Use exact declared signal names without a leading `_` (`d.wire("valid")` -> `valid=1`, never `v=1`; pass `label="_valid"` if a leading underscore should appear in labels), initialize signals in `init=` (never leave signals un-driven at default `"x"`), and note `d.posedge()`/`delay()`/`at()` return a `Wait` handle that must be called `(...)` to assign.

## TUI Reply Workflow (`python_wave`) & Self-Check

1. Write code inside ```` ```python_wave ```` assigning the diagram to `d` (or last `Diagram` variable). **Do NOT** call `print(d.to_svg())` or `d.save(...)` unless the user requested a file on disk.
2. **Put all explanations inside the diagram**: `extensions/waveview.ts` replaces the entire fence with rendered SVG, so `#` comments are invisible. Use `d.note`, `d.caption`, `d.arrow`, `d.region`, `d.marker`, `d.measure` only—never fake rows or ASCII/SVG hacks, and avoid repeating edge-by-edge prose outside the diagram when the diagram already shows it.
   - **Handshake `note` pitfall**: never anchor `d.note(e_hs.valid, ...)` if `valid` drops `1→0` at `e_hs` (dot lands on falling edge); anchor on `d.note(clk, e_hs, ...)` or steady `d.note(ready, e_hs, ...)`, or use `d.marker(e_hs, ...)`.
3. **Verify via stdin before replying** (renderer errors do **not** return to your context; prefix `uv run` if not globally installed):

```bash
waveview check - --strict <<'EOF'
# paste exact python_wave block content (or full markdown fence)
EOF
```

Confirm before replying (Zero PNG Policy — **never** generate or read PNGs to inspect):
- Exit `0` (`[waveview check] OK`, `svg=valid`)
- Zero `truncated_labels`, `dropped_labels`, `crowded_arrows`, `crowded_notes`, `crowded_regions`
- `@posedge` sample table & `describe()` steps match the intended protocol (or check `waveview check <script.py> --format json` / `d.to_dict()` / `d.inspect("json")` programmatically).

If `truncated_labels` or crowding occurs: use `width=900` (or `max_width=None` when hitting `2400px`), `pad=False` on wide buses, `d.gap(...)` for idle spans, or shorten labels / move text to `d.caption(...)`.

## References (Read On Demand Only)

Only open [references/api-and-schema.md](references/api-and-schema.md) when you specifically need gated-clock `enable`/`disable` details, the full `waveview check --format json` schema keys, or copy-ready multi-channel protocol templates (Setup/Hold/`tco`, AXI-Stream, 2-FF CDC, SPI, AXI4 Write Burst, AXI4-Lite Read).
