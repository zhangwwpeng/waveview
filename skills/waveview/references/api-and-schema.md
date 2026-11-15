# waveview API & Schema Reference

## 1. Diagram Construction & Signals

```python
from waveview import Diagram

d = Diagram(
    title: str | None = None,
    timescale: str = "ns",        # "ps" | "ns" | "us" | "ms" | "s"
    end: str | int | float | None = None,
    theme: str = "light",         # "light" | "dark" | "bamboo"
    px_per_ns: float | None = None,
    warn_noop: bool = True,
    max_width: float | None = 2400.0,  # None uncaps auto-widening for wide 32/64-bit buses
)
```

### Declarations & Groups

- `d.clock(name, freq=None, period=None, phase=0, duty=0.5, label=None, idle=0, start=None, stop=None, gated=False, tco=0) -> Clock`
  - Specify **exactly one** of `freq` (e.g. `"100MHz"`) or `period` (e.g. `"10ns"`).
  - `tco`: default register clock-to-output propagation delay applied after `d.posedge(clk)` / `d.negedge(clk)` (`0 <= tco < period`). Can be overridden per wait via `d.posedge(clk, tco="0ns")`.
  - Gated clock options:
    - Set `idle=0` or `idle=1`, and pass `start=`/`stop=` or `gated=True` with `clk.enable(at=None)` / `clk.disable(at=None)` (default `at` = `d.now`).
- `d.wire(name, init="x", label=None) -> Signal` (allowed values: `0`, `1`, `"0"`, `"1"`, `True`, `False`, `"x"`, `"z"`).
- `d.bus(name, init="x", width=None, fmt="hex", label=None, pad=True) -> Signal` (`fmt`: `"hex"` | `"dec"` | `"bin"`; values: non-negative `int`, `str`, `"x"`, `"z"`; set `pad=False` to omit leading zeros e.g. `0x270F` on a `width=32` bus while retaining full 32-bit range checks).
- `with d.group("Group Label"): ...` — nests row groups on the left rail.

## 2. Advancing Time & Assignments

Wait methods advance/move `d.now` and return a `Wait` object; call the `Wait` object to assign signals and obtain an `Event` handle, or advance `d.now` cleanly and call `d.set(...)`:

- `d.posedge(clk, n=1, *, tco=None)(**values) -> Event`
- `d.negedge(clk, n=1, *, tco=None)(**values) -> Event`
- `d.delay(t)(**values) -> Event`
- `d.at(t)(**values) -> Event` (warns if jumping backward relative to `d.now`)
- `d.gap(t)(**values) -> Event` (compresses long idle interval with a visual break mark)
- `d.set(**values) -> Event` (assign at current `d.now`)
- `d.now -> Instant` (current cursor timestamp)

Event handles & non-mutating `Point` / `TimeRef` anchors (**no hardcoded `"30ns"` needed**):
- **Every `Event`, `Point`, `Wait`, `Instant`, or `clk.posedge(n)` / `clk.negedge(n)` can be passed directly as `TimeRef`** to `d.note(row, ref, text)`, `d.region(row, start, end)`, `d.marker(ref)`, `d.measure(start, end)`, `d.point(row, ref)`, `sig.at(ref)`, and the second item of an `arrow` tuple `(row, ref)` — never hand-calculate `"30ns"` when an event handle already exists for that beat.
- `e.req` / `e["req"]` returns a `Point(signal, time)` for that assignment. Only signals assigned in that specific call exist on `e`. If `warn_noop=False`, same-value assignments still return `e.sig` for anchoring without adding `1→1` noise to `describe()`.
- `clk.posedge(n=1)` / `clk.negedge(n=1)` returns a non-mutating `Point(clk, edge_time)` at the 1-based `n`-th active edge (works for gated clocks too).
- `clk.at(t)` / `sig.at(t)` / `d.point(row_or_name, at=None)` returns a non-mutating `Point` at `t` (or `d.now`).

## 3. Annotations & Rendering

- `d.arrow(src: PointRef, dst: PointRef, label: str | None = None)` — accepts any `Point` (e.g. `e1.req`, `clk.posedge(2)`) **or** `(row_or_name, TimeRef)` tuple (e.g. `(ready, e_hs)` or `("awready", clk.posedge(4))` when a signal stays unchanged on the handshake beat).
- `d.region(row: Row | str, start: TimeRef, end: TimeRef, label: str | None = None)`
- `d.marker(at: TimeRef | None = None, label: str | None = None)` (`at=None` uses `d.now`)
- `d.measure(start: TimeRef, end: TimeRef, label: str | None = None)`
- `d.note(target: Point, label: str)` or `d.note(row: Row | str, at: TimeRef, label: str)` — single-point callout dot + leader line + pill badge (for concise on-wave notes without full-height marker lines)
- `d.caption(*texts: str)` — explanatory footer note line(s) rendered below the bottom time axis (multiline strings split on `\n`; automatically expands SVG canvas height/width)
- `d.describe() -> str` — chronological action playback (`old->new`).
- `d.check() -> None` — emits `WaveWarning` if content exceeds `end` or any signal remains un-driven with `init="x"`.
- `d.to_svg() -> str` / `d.to_png(scale=2) -> bytes` / `d.save(path, scale=2) -> None` (`.svg` or `.png`).

## 4. Self-Check & Timing Recognition API

### Python Methods on `Diagram`

- `d.sample(clk, edge="rise", *, include_gaps=False) -> list[dict]`
  - Samples every signal at each active rising (`"rise"`) or falling (`"fall"`) edge of `clk` in `[0, d.end)`.
  - Each entry:
    ```json
    {
      "edge": 1,
      "time": "5ns",
      "time_s": 5e-9,
      "signals": {
        "valid": {"sampled": "0", "after": "1", "changed": true}
      }
    }
    ```
  - `sampled`: signal value strictly before the edge (`value_before(t)`).
  - `after`: signal value at the edge (`value_at(t)`).
  - `changed`: `True` when `sampled != after`.
- `d.to_dict() -> dict` — full schema documented below.
- `d.inspect(fmt="text") -> str` — `"text"` (AI checklist + `describe()` + `@posedge` sample tables) or `"json"`.

### CLI (`waveview`)

- `waveview check [source="-"] [--format text|json] [--strict]`
  - `source`: path to `.py` script, Markdown snippet, or `-` for stdin.
  - Automatically extracts code from ```` ```python_wave ```` / ```` ```waveview ```` / fenced blocks if Markdown is piped in, and runs with `__name__ = "__waveview_cli__"` so `if __name__ == "__main__":` blocks are skipped.
  - Picks `d` if it is a `Diagram`, otherwise the last `Diagram` instance created in module globals.
  - Exit codes:
    - `0`: valid SVG (and no warnings/layout issues, or non-strict mode).
    - `1`: syntax/runtime error, `WaveError`, missing `Diagram`, invalid SVG XML, or file read error.
    - `2`: `--strict` enabled and `warnings` or `layout_issues` is non-empty (`ok == False`).
- `waveview render [source="-"] [-o|--output <path|"-">] [--scale 2.0]`
  - Renders `.svg` or `.png` to `<path>`, or writes SVG to stdout when `-o -` (default).

### `waveview check --format json` / `d.to_dict()` Schema

```json
{
  "ok": true,
  "svg_valid": true,
  "svg_error": null,
  "title": "APB3 Transfer",
  "timescale": "ns",
  "theme": "bamboo",
  "end": "180ns",
  "canvas": {
    "width": 959.4,
    "height": 652.0,
    "px_per_ns": 4.2188,
    "hit_width_cap": false,
    "truncated_labels": [],
    "dropped_labels": [],
    "crowded_arrows": [],
    "crowded_notes": [],
    "crowded_regions": []
  },
  "warnings": [],
  "layout_issues": [],
  "clocks": [
    {
      "name": "clk",
      "label": "clk",
      "freq": "50MHz",
      "period": "20ns",
      "phase": "0ns",
      "duty": 0.5,
      "tco": "0ns",
      "gated": false,
      "idle": null,
      "windows": [],
      "groups": ["Clock & Reset"]
    }
  ],
  "signals": [
    {
      "name": "paddr",
      "label": "PADDR",
      "kind": "bus",
      "width": 16,
      "fmt": "hex",
      "pad": true,
      "init": "x",
      "groups": ["APB"],
      "segments": [
        {"start": "0ns", "end": "20ns", "value": "x"}
      ]
    }
  ],
  "steps": [
    {
      "time": "20ns",
      "cause": "clk↑#2",
      "changes": [{"signal": "paddr", "old": "x", "new": "0x0010"}]
    }
  ],
  "clock_samples": {
    "clk": [
      {
        "edge": 1,
        "time": "0ns",
        "time_s": 0.0,
        "signals": {
          "paddr": {"sampled": "x", "after": "x", "changed": false}
        }
      }
    ]
  },
  "gaps": [{"start": "80ns", "end": "140ns"}],
  "annotations": [
    {"type": "arrow", "src": "valid@10ns", "dst": "ready@20ns", "label": "accept"},
    {"type": "note", "target": "ready@20ns", "row": "ready", "time": "20ns", "label": "slave ready"}
  ],
  "captions": ["Note: Transfer occurs on the clock edge where both valid and ready are 1."]
}
```

## 5. Copy-Ready Protocol Templates

### Setup / Hold / $t_{co}$ Register Timing

```python
from waveview import Diagram

d = Diagram("Flip-Flop Setup / Hold / tco", theme="bamboo")
clk = d.clock("clk", period="10ns", tco="1.5ns")
d_in = d.wire("d", init=0, label="D (in)")
q_out = d.wire("q", init=0, label="Q (out)")

e_setup = d.at("7ns")(d=1)
e_q = d.posedge(clk)(q=1)      # clk↑#2 @10ns -> Q toggles @11.5ns (tco=1.5ns)
e_hold = d.at("13ns")(d=0)
d.posedge(clk)(q=0)            # clk↑#3 @20ns -> Q toggles @21.5ns

d.measure(e_setup, clk.posedge(2), "t_su")
d.measure(clk.posedge(2), e_hold, "t_h")
d.arrow(clk.posedge(2), e_q.q, "t_co")
d.note(clk.posedge(2), "posedge #2")
d.caption("Note: Q switches 1.5ns after clk↑ due to flip-flop clock-to-Q delay (t_co).")
```

### Valid / Ready Handshake

```python
from waveview import Diagram

d = Diagram("AXI-Stream Handshake", theme="bamboo")
clk = d.clock("clk", freq="100MHz")
valid = d.wire("valid", init=0)
ready = d.wire("ready", init=0)
data = d.bus("data", init="x", width=8)

e_req = d.posedge(clk)(valid=1, data=0xA1)
e_rdy = d.posedge(clk)(ready=1)
e_hs = d.posedge(clk)(data=0xB2)
d.arrow(e_req.valid, e_hs.data, "transfer 1")
d.note(e_rdy.ready, "slave ready")
d.posedge(clk)(valid=0, ready=0, data="x")
d.caption("Note: Transfer beats occur at @posedge clk when valid == 1 && ready == 1.")
```

### Dual-Clock CDC (2-FF Synchronizer)

```python
from waveview import Diagram

d = Diagram("2-FF CDC Sync", theme="light")
clk_a = d.clock("clk_a", freq="100MHz")
clk_b = d.clock("clk_b", freq="70MHz", phase="2ns")
src_pulse = d.wire("src_pulse", init=0)
sync1 = d.wire("sync1", init=0)
sync2 = d.wire("sync2", init=0)

e0 = d.posedge(clk_a)(src_pulse=1)
d.posedge(clk_b)(sync1="x")
d.region(sync1, d.now, "30ns", "metastable")
d.posedge(clk_a)(src_pulse=0)
e1 = d.posedge(clk_b)(sync1=1)
e2 = d.posedge(clk_b)(sync2=1)
d.arrow(e1.sync1, e2.sync2, "2nd FF")
```

### Gated SPI Burst

```python
from waveview import Diagram

d = Diagram("SPI Mode 0 Write", theme="bamboo", warn_noop=False)
sclk = d.clock("sclk", freq="25MHz", idle=0, gated=True)
cs_n = d.wire("cs_n", init=1)
mosi = d.wire("mosi", init=0)

d.at("20ns")(cs_n=0, mosi=1)
sclk.enable("40ns")
for bit in (0, 1, 1, 0):
    d.negedge(sclk)(mosi=bit)
d.posedge(sclk)
sclk.disable(d.now)
d.delay("20ns")(cs_n=1, mosi=0)
```

### AXI4 Multi-Channel Write Burst (AW + W 2-beat + B)

Notice the RTL sampling rule: **assignments at `posedge k` settle after edge `k` and are sampled (`sampled`) at `posedge k+1`**. When `READY` already holds `1` on the handshake edge (so `READY` has no assignment in `e_hs`), pass `(awready, e_aw_hs)` or `d.point(awready, e_aw_hs)` instead of `e_aw_hs.awready`.

```python
from waveview import Diagram

d = Diagram("AXI4 Write Burst (AW + W 2-beat + B)", theme="bamboo")
clk = d.clock("aclk", freq="100MHz")

with d.group("AW"):
    awvalid = d.wire("awvalid", init=0, label="AWVALID")
    awready = d.wire("awready", init=0, label="AWREADY")
    awaddr = d.bus("awaddr", init="x", width=32, pad=False, label="AWADDR[31:0]")

with d.group("W"):
    wvalid = d.wire("wvalid", init=0, label="WVALID")
    wready = d.wire("wready", init=0, label="WREADY")
    wdata = d.bus("wdata", init="x", width=32, pad=False, label="WDATA[31:0]")
    wlast = d.wire("wlast", init=0, label="WLAST")

with d.group("B"):
    bvalid = d.wire("bvalid", init=0, label="BVALID")
    bready = d.wire("bready", init=0, label="BREADY")
    bresp = d.bus("bresp", init="x", label="BRESP")

d.posedge(clk)
e_aw = d.posedge(clk)(awvalid=1, awaddr=0x2000)
e_w0 = d.posedge(clk)(awready=1, wvalid=1, wdata=0x270F)
e_aw_hs = d.posedge(clk)(awvalid=0, awaddr="x", wready=1)
e_w0_hs = d.posedge(clk)(wdata=0x2710, wlast=1, bready=1)
e_w1_hs = d.posedge(clk)(wvalid=0, wready=0, wdata="x", wlast=0)
e_b = d.posedge(clk)(bvalid=1, bresp="OKAY")
e_b_hs = d.posedge(clk)(bvalid=0, bresp="x")
d.posedge(clk)(awready=0, bready=0)

d.arrow(e_aw.awvalid, (awready, e_aw_hs), "AW hs")
d.arrow(e_w1_hs.wlast, e_b.bvalid, "Wlast -> B")
d.arrow(e_b.bvalid, (bready, e_b_hs), "B hs")
d.region(wdata, e_w0, e_w1_hs, "W 2-beat")
d.note(e_aw_hs.awvalid, "sampled both=1")
d.caption(
    "Note 1: Assignments on edge k take effect after edge k and are sampled on edge k+1.",
    "Note 2: (awready, e_aw_hs) and (bready, e_b_hs) anchor arrows on edges where READY stays constant.",
)
```
