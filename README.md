# waveview

用接近 Verilog testbench 的过程式 Python 写法描述数字时序图，直接渲染为高质量 SVG / PNG，并生成结构化文本摘要用于校对。

```python
from waveview import Diagram

d = Diagram(title="valid/ready 握手", theme="bamboo")
clk   = d.clock("clk", freq="100MHz")
valid = d.wire("valid", init=0)
ready = d.wire("ready", init=0)
data  = d.bus("data", width=8, label="data[7:0]")

d.posedge(clk)
e1 = d.posedge(clk)(valid=1, data=0xA5)   # @(posedge clk) valid <= 1; data <= 8'hA5;
e2 = d.posedge(clk, 2)(ready=1)           # repeat(2) @(posedge clk) ready <= 1;
d.posedge(clk)(valid=0, ready=0, data="x")

d.arrow(e1.valid, e2.ready, "2 拍后响应")
d.save("out/handshake.png")               # 自动创建父目录，支持 .svg / .png
print(d.describe())
```

## 核心特性

- **精确时间引擎**：所有时刻内部使用 `Fraction` 精确分数秒表示，100MHz 与 143MHz 等无理/非整除异步时钟运行上千周期也不产生浮点累积误差。
- **过程式时序语法**：
  - 边沿推进：`d.posedge(clk, n)` / `d.negedge(clk, n)`
  - 延时与跳转：`d.delay("2ns")` / `d.at("63ns")` / `d.set(...)`
  - 空闲折叠：`d.gap("1us")` 自动画成波浪断口并压缩长等待区间
- **自由运行时钟与门控时钟**：
  - 支持 `freq` / `period`、`phase`（支持时间或 `90deg`/`90°`）、`duty`
  - 支持 `start`/`stop`/`gated` 与 `idle=0|1`，以及在时间流中动态调用 `clk.enable()` / `clk.disable()`（适配 SPI `SCLK`、I2C `SCL` 等）
- **完整的数字信号与分组**：
  - `wire` 支持 `0`、`1`、`"x"`（不定态斜线填充）、`"z"`（高阻中心虚线）
  - `bus` 支持自动位宽校验、`hex`/`dec`/`bin` 格式化（可用 `pad=False` 省略前导零），或任意状态名字符串；支持 `Diagram(..., width=900)` 自适应目标画布宽（保留不截断硬约束并在 `width_drivers` 中报告撑宽原因），或按段宽自动展宽（默认上限 `max_width=2400`，可传 `None` 放开）
  - `with d.group("名称"):` 支持多级嵌套左侧括号分组
- **丰富的标注与自动防重叠排版**：
  - `d.arrow(src, dst, "...")`：因果曲线箭头（支持信号跳变 `e1.valid`、时钟锚点 `clk.posedge(2)` 及 `(row, TimeRef)` 二元组如 `(ready, e_hs)`），自动避让总线值、高亮标注与其他箭头文字
  - `d.note(e1.valid, "...")` / `d.note(clk.posedge(3), "...")` / `d.note(ready, e_hs, "...")`：单点引线气泡注释，紧贴波形锚点、不拉通栏竖线且自动防重叠
  - `d.caption("注1：...", "注2：...")`：底部图注，在时间轴下方显示多行说明并自动撑高/撑宽画布
  - `d.region(sig, start, end, "...")`：区间高亮框
  - `d.marker(at, "...")`：全行虚线标记（密集标记自动错层）
  - `d.measure(start, end, "...")`：时段双向标注（互不重叠的测量自动复用同一行）
- **时钟边沿锚点与 `tco` 输出延迟**：支持 `clk.posedge(n)` / `clk.negedge(n)` / `sig.at(...)` / `d.point(...)` 非推进式锚点定位，以及 `d.clock(..., tco="1.5ns")` / `d.posedge(clk, tco=...)` 表达触发器传播延时。
- **三套高对比主题**：内置 `light`（浅色文档）、`dark`（深色夜景）、`bamboo`（竹林青瓷），且同一页面嵌入多张不同主题 SVG 时 `<defs>` 互不串色。
- **多端渲染与核对闭环**：
  - `d.save("a/b.png" | "a/b.svg")`（自动创建缺失父目录）、`d.to_svg()`、`d.to_png(scale=2)`
  - 支持 Jupyter / IPython / Marimo 自动内联显示（`_repr_svg_()`）
  - `d.describe()` 输出按时间排序的状态跳变清单，方便人工或 AI 校对时序是否写错

## 安装

需要 Python `>= 3.12`。推荐用 `uv tool` 安装为全局 `waveview` CLI（装到 `~/.local/bin/waveview`，Skill、TUI 插件与任意项目目录共用这一个命令）：

```bash
# 本地开发源码（推荐：可编辑安装，改完 src/ 立即生效）
uv tool install -e .

# 或在新机器上直接从 Git 仓库安装（无需先手动 clone 或建虚拟环境）
uv tool install git+https://github.com/zhangwwpeng/waveview
```

若使用 `omp` TUI 内联渲染波形图，安装插件并在 `~/.omp/agent/config.yml` 开启 `extensionRenderers.assistantText: true`：

```bash
# 从 GitHub 直接安装（或本地仓库根目录执行 omp plugin link . 并定期 omp update 同步内置 skill）
omp install github:zhangwwpeng/waveview
```

若要在仓库内开发与跑测试，可使用项目虚拟环境：

```bash
uv sync
```

## AI 自检、时序识别与 Skill

为了让大模型在 TUI 中稳定写出正确波形、免去生成/读取 PNG 截图即可自检排版并按拍识别时序，全局安装 `waveview` CLI 后即可在任意项目下直接使用（未装全局 CLI 时在仓库内可用 `uv run waveview ...`）：

```bash
waveview check examples/apb.py               # 校验 SVG 合法性、布局拥挤/截断、并打印 @posedge 采样表
waveview check examples/apb.py --format json # 输出完整 JSON（含 canvas、clock_samples、steps）
waveview check - --strict <<'EOF'            # 直接校验 stdin 中的 Python 或 ```python_wave 代码块
...
EOF
waveview render examples/apb.py -o out/apb.svg
```

- **Python 编程核对**：任意 `Diagram` 支持 `d.sample("clk")`（返回每个有效边沿的 `sampled`→`after` 寄存器采样变化）、`d.handshakes(...)` / `d.expect_handshake(...)` / `d.expect_stable(...)` 协议断言、`d.to_dict()` 和 `d.inspect("text" | "json")`。
- **内置 Skill**：见 [skills/waveview/SKILL.md](skills/waveview/SKILL.md)。
- **omp TUI 渲染插件**：见 [extensions/waveview.ts](extensions/waveview.ts)（通过 `omp install github:zhangwwpeng/waveview` 或本机 `omp plugin link .` 安装，将回复中的 `python_wave` 代码块内联渲染为 SVG）。

## 文档导航

- **[完整用户指南 (docs/guide.md)](docs/guide.md)**：涵盖时间/频率/相位单位、门控时钟、所有 API 参数说明、主题配色键、AI 自检与时序采样、排错 FAQ 与协议实战示例。
- **[设计文档 (docs/design.md)](docs/design.md)**：动机、设计取舍与实现进度。
- **[waveview Skill (skills/waveview/SKILL.md)](skills/waveview/SKILL.md)**：TUI 内联画图闭环、`waveview check` 自检流程与 JSON Schema 速查。
- **[omp 渲染插件源码 (extensions/waveview.ts)](extensions/waveview.ts)**：将 `python_wave` 围栏在 TUI 中渲染为内联波形图的扩展实现。
- **示例脚本（`examples/`）**：
  - [handshake.py](examples/handshake.py) —— 单时钟 `valid`/`ready` 握手与因果箭头
  - [cdc.py](examples/cdc.py) —— 异步双时钟两级触发器同步与亚稳态高亮
  - [burst.py](examples/burst.py) —— 总线突发传输、`gap` 折叠、三态信号与深色主题
  - [spi.py](examples/spi.py) —— 手动翻转 `SCLK` 的 SPI 模式 0
  - [gated_spi.py](examples/gated_spi.py) —— 使用门控时钟与 `warn_noop=False` 的简洁 SPI 写法
  - [apb.py](examples/apb.py) —— 多级分组的 APB3 读写传输（`bamboo` 主题）

## 开发与测试

```bash
uv run waveview check examples/apb.py      # 布局自检 + 时序文本识别报告
uv run waveview render examples/cdc.py -o out/cdc.svg
uv run python examples/cdc.py              # 渲染示例并输出到 out/
uv run pytest                              # 运行单元测试与快照比对
UPDATE_SNAPSHOTS=1 uv run pytest           # 确认渲染改动符合预期后更新快照
```
