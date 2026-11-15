# waveview 用户指南

`waveview` 是一个用接近 Verilog testbench 的过程式 Python 语法描述数字电路时序图的库。你只需要按时间先后顺序描述“等待某个边沿或延时、驱动信号变化”，就能得到高对比度的 SVG / PNG 时序图，并用纯文本清单核对每一拍的信号跳变。

---

## 目录

- [1. 安装与 3 分钟快速上手](#1-安装与-3-分钟快速上手)
- [2. 时间游标与精确分数时间模型](#2-时间游标与精确分数时间模型)
- [3. `Diagram` 构造参数详解](#3-diagram-构造参数详解)
- [4. 时钟、信号与分组声明](#4-时钟信号与分组声明)
- [5. 过程式等待与信号赋值](#5-过程式等待与信号赋值)
- [6. 事件句柄 `Event` 与四类时序标注](#6-事件句柄-event-与四类时序标注)
- [7. 主题配色与自定义扩展](#7-主题配色与自定义扩展)
- [8. 导出、Notebook 集成与校对工具](#8-导出notebook-集成与校对工具)
- [9. 典型数字协议实战示例](#9-典型数字协议实战示例)
- [10. 常见报错与排查速查表](#10-常见报错与排查速查表)

---

## 1. 安装与 3 分钟快速上手

环境要求：Python `>= 3.12`（底层 PNG 光栅化依赖 `resvg-py`，会随系统字体渲染中文标签）。

```bash
uv sync          # 推荐：安装运行与测试依赖
# 或
pip install -e .
```

一个完整的 `valid/ready` 握手示例：

```python
from waveview import Diagram

d = Diagram(title="valid/ready 握手", theme="bamboo")
clk   = d.clock("clk", freq="100MHz")
valid = d.wire("valid", init=0)
ready = d.wire("ready", init=0)
data  = d.bus("data", init="x", width=8, label="data[7:0]")

d.posedge(clk)                              # t = 0ns（第 1 个上升沿）
e1 = d.posedge(clk)(valid=1, data=0xA5)     # t = 10ns（第 2 个上升沿）
e2 = d.posedge(clk, 2)(ready=1)             # t = 30ns（再等 2 拍）
d.posedge(clk)(valid=0, ready=0, data="x")  # t = 40ns（释放信号）

d.arrow(e1.valid, e2.ready, "2 拍后响应")
d.save("out/handshake.png")                 # 若 out/ 不存在会自动创建
print(d.describe())
```

---

## 2. 时间游标与精确分数时间模型

`Diagram` 内部维护一个单向推进的时间游标（`cursor`），初始处于 `t = 0` 之前；所有时刻内部都用 Python 标准库 `fractions.Fraction`（有理数秒）保存。因此即使在 100MHz（周期 `10ns`）与 143MHz（周期 `1000/143 ns`）等非整除异步时钟下推进上千个周期，边沿时刻也永远精确对齐、绝不产生浮点漂移。

### 时间、频率与相位格式

| 类型 | 可用单位 | 说明与示例 |
| :--- | :--- | :--- |
| **时间** | `fs`、`ps`、`ns`、`us`（或 `µs`/`μs`）、`ms`、`s` | 大小写不敏感，支持科学计数与数字下划线；不带单位的纯数字按 `timescale` 解析。<br>例：`"10ns"`、`"10NS"`、`"1_000ns"`、`"1.25µs"`、`"2.5"`、`20` |
| **频率** | `Hz`、`kHz`、`MHz`、`GHz` | 大小写不敏感；不带单位的纯数字按 `Hz` 解析，必须 `> 0`。<br>例：`"100MHz"`、`"1.5GHz"`、`100_000_000` |
| **相位** | 同时间单位，或角度 `deg` / `°` | 角度按周期百分比换算（支持不区分大小写的 `deg` 与 `°`），并对周期取模归入 `[0, period)`。<br>例：`"2ns"`、`"90deg"`、`"90°"`、`"-45deg"` |

> [!TIP]
> 尽量使用 `"100MHz"`、`phase="90deg"` 或字符串时间 `"2.5ns"`，可读性比裸数字更强，也不受更换 `timescale` 的影响。

---

## 3. `Diagram` 构造参数详解

```python
Diagram(
    title: str | None = None,
    timescale: str = "ns",
    end: str | int | float | None = None,
    theme: str = "light",
    px_per_ns: float | None = None,
    warn_noop: bool = True,
    max_width: float | None = 2400.0,
)
```

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `title` | `None` | 画布左上角标题文本；若较长，SVG 总宽度会自动扩宽以避免右侧被裁切。 |
| `timescale` | `"ns"` | 坐标轴显示单位，同时作为裸数字时间的默认单位（可选 `fs`/`ps`/`ns`/`us`/`ms`/`s`）。 |
| `end` | `None` | 画布结束时刻（必须 `> 0`）。为 `None` 时自动计算：有时钟则为最后时刻之后再留 **2 个最快时钟周期**；无时钟则留 `max(last / 10, 10 * unit)`。显式指定更小的 `end` 时，`check()` 会发出警告，超出 `end` 的部分不会绘制。 |
| `theme` | `"light"` | 配色主题，内置 `"light"`、`"dark"`、`"bamboo"`（详见[第 7 节](#7-主题配色与自定义扩展)）。 |
| `px_per_ns` | `None` | 手动指定每秒的水平缩放比例（每纳秒像素数，必须为有限正数）。为 `None` 时自动计算：确保最窄时钟半周期清晰，并按总线（`bus`）各**可见分段**的最窄文字需求自动加宽（受 `max_width` 限制）。 |
| `warn_noop` | `True` | 是否在“信号被赋予了与当前值相同的值”时发出 `WaveWarning`。设为 `False` 时不仅静音告警，`describe()` / `inspect()` 也会自动省略没有实际跳变的 `1→1` 噪声，同时仍保留返回事件 `e.sig` 的点句柄。 |
| `max_width` | `2400.0` | 自动缩放时允许的最大时间轴像素宽度（单位 px；传 `None` 可完全放开自动展宽，避免 32/64 位长总线图截断）。 |

---

## 4. 时钟、信号与分组声明

### 标识符与显示名：`name` vs `label`

所有时钟和信号的第一个参数 `name` 必须是合法的 **Python 标识符**（字母/数字/下划线且不以数字开头、非 Python 关键字），因为在赋值和读取事件句柄时你要直接把它当作关键字参数或属性来写（如 `e1.data` 或 `(data=0xA5)`）。

如果需要展示总线位宽、反相斜杠或中文名，请传入 `label=`：

```python
data = d.bus("data", width=8, label="data[7:0]")
cs_n = d.wire("cs_n", init=1, label="CS_n")
```

### 4.1 时钟 `d.clock()`（含门控时钟）

```python
clk = d.clock(
    name: str,
    *,
    freq: str | int | float | None = None,
    period: str | int | float | None = None,
    phase: str | int | float = 0,
    duty: float = 0.5,
    label: str | None = None,
    idle: Literal[0, 1] = 0,
    start: TimeRef | None = None,
    stop: TimeRef | None = None,
    gated: bool = False,
    tco: str | int | float | Fraction = 0,
)
```

- `freq` 与 `period` **必须二选一**，不能同时写或都不写。
- `duty` 必须在 `(0, 1)` 开区间内（不含 `0` 和 `1`）。
- `tco` 可选，表示同步触发器相对时钟沿的默认输出传播延迟（`0 <= tco < period`）；调用 `d.posedge(clk, tco=...)` 或 `d.negedge(clk, tco=...)` 也可针对单次等待覆盖该延迟。
- 图中声明的**第一个时钟**会在波形区背景生成竖向浅色参考网格（在其激活的上升沿位置，前提是周期宽度 `>= 10px`）。

#### 门控时钟（Gated Clock）

画 SPI（`SCLK`）或 I2C（`SCL`）等“只在传输期间翻转，空闲时停留在固定电平”的时钟时，不再需要当成普通 `wire` 手动循环翻转，可以通过以下两种方式声明门控时钟：

**方式一：声明时指定静态窗口 `start` / `stop`**

```python
# 只在 20ns ~ 420ns 期间翻转，窗口之外保持 idle=0
sclk = d.clock("sclk", period="50ns", phase="45ns", idle=0, start="20ns", stop="420ns")
```

**方式二：声明为 `gated=True`，在时间流中动态 `enable()` / `disable()`**

```python
sclk = d.clock("sclk", period="50ns", phase="45ns", idle=0, gated=True, label="SCLK")

d.at("20ns")(cs_n=0)
sclk.enable()          # 不传参时默认使用当前游标 d.now (20ns)
for _ in range(8):
    d.negedge(sclk)(...)
sclk.disable()         # 从当前游标时刻起关闭时钟，回到 idle 电平
```

`idle` 默认为 `0`，也可设为 `1`（例如 I2C 的 `SCL` 空闲拉高）。`enable(at=...)` / `disable(at=...)` 也接受具体时间字符串、`Event` 或 `Point` 等任意 `TimeRef`，支持多段开关窗口。在窗口边界如果发生实际电平切换（如 `idle=1` 时钟从 `1` 切入高/低相位的 `0`），也会精确生成对应边沿。

### 4.2 单比特线 `d.wire()`

```python
req = d.wire("req", init=0, label=None)
```

合法值仅有四种：
- `0` / `False` / `"0"` → 低电平 `0`
- `1` / `True` / `"1"` → 高电平 `1`（上方填充柔和渐变阴影）
- `"x"` / `"X"` → 不定态/亚稳态（斜线阴影矩形框）
- `"z"` / `"Z"` → 高阻态（垂直居中的虚线）

### 4.3 多比特总线 `d.bus()`

```python
addr = d.bus("addr", init="x", width=16, fmt="hex", label="addr[15:0]", pad=True)
```

- `fmt` 支持 `"hex"`（默认，如 `0x0024`）、`"dec"`（十进制）、`"bin"`（如 `0b10100101`）；默认 `pad=True` 按 `width` 补前导零，设为 `pad=False` 时保留 `width` 位宽范围检查但省略前导零（如 32 位总线显示为 `0x270F` 而非 `0x0000270F`）。
- 若传入整数且声明了 `width`，超出 `[0, 2^width - 1]` 或传入负数都会在运行时立刻抛出友好的 `WaveError` 检查位宽溢出。
- 也可以直接传任意文本状态（例如 `"IDLE"`、`"HEADER"`、`"CRC"`），以及 `"x"` / `"z"`。

### 4.4 嵌套分组 `with d.group()`

```python
with d.group("主机接口"):
    with d.group("控制通道"):
        psel = d.wire("psel", init=0)
        penable = d.wire("penable", init=0)
    paddr = d.bus("paddr", width=32)
```

`group()` 支持任意层级嵌套，左侧会画出分组标题与圆角折线括号，奇数行斑马纹也会自动从分组括号右侧开始，不会切断分组竖线。

---

## 5. 过程式等待与信号赋值

### 推进游标的五种等待方法

| 方法 | 语义（类比 Verilog） | 说明 |
| :--- | :--- | :--- |
| `d.posedge(clk, n=1, *, tco=None)` | `repeat(n) @(posedge clk)` | 严格向前寻找第 `n` 个上升沿（游标尚未移动时可落在 `t = 0`），并额外前进 `tco`（默认取 `clk.tco`）。`clk` 可传对象或时钟名字符串。 |
| `d.negedge(clk, n=1, *, tco=None)` | `repeat(n) @(negedge clk)` | 严格向前寻找第 `n` 个下降沿，并额外前进 `tco`。 |
| `d.delay(t)` | `#t` | 在当前游标基础上向后推移 `t`（`t >= 0`）。 |
| `d.at(t)` | 跳转到绝对时刻 `t` | 常用于异步复位或异步事件。若 `t < 当前游标` 会发出 `WaveWarning` 提示时间往回走，但允许插入且 `describe()` 仍会按真实时间顺序准确呈现 `old→new`。 |
| `d.gap(t)` | 推进 `t` 并折叠绘制 | 游标前进 `t`（`t > 0`），画布上把这段漫长空闲挤压为带有双波浪断口的窄带，适合展示数微秒等待前后的两个突发。 |

### 赋值与读取当前时刻

- **等待并同时赋值**（最常用）：等待方法返回 `Wait` 对象，直接紧跟括号传信号赋值，返回该时刻的 `Event`：
  ```python
  e1 = d.posedge(clk)(valid=1, data=0xA5)
  ```
- **先等后赋 / 分步赋值**：
  ```python
  d.posedge(clk)                # 只推进时间，不改变信号
  d.set(req=1)                  # 在当前游标时刻赋值
  ```
  如果在任何等待前就调用 `d.set(req=1)`，游标会初始化到 `t = 0`，紧跟的 `d.posedge(clk)` 会自然推进到下一个上升沿（不会重复撞在 `t = 0`）。
- **循环技巧**：用循环驱动比特流或突发数据时，若不想反复判断某位是否变化，只需在构造时写 `Diagram(..., warn_noop=False)`；即便连续两拍赋了相同的值，渲染器也会自动把相邻相同电平/数据值合并为同一段，不会画出多余的 `><` 假跳变。
- **读取当前时间**：`d.now` 返回一个 `Instant` 对象（可传给 `region`、`marker`、`measure` 等）。

> [!NOTE]
> 同一个信号在同一个时刻不能被赋值两次（否则抛 `WaveError`），但允许不同时刻分别修改同一个或不同信号。

---

## 6. 事件句柄 `Event` 与时序标注

每次赋值（`d.posedge(...)(...)`、`d.set(...)` 等）都会返回一个 `Event` 实例 `e`：

- 通过 `e.req`、`e["req"]` 或 `e[req_signal]` 可以拿到指向该信号在这次跳变瞬间的 **`Point`** 句柄。
- 除了从 `Event` 获取外，还可以随时通过不改变游标 `d.now` 的非推进式方法直接取 **`Point`** 锚点（可直接传给 `arrow`、`note`、`measure`、`marker`、`region`）：
  - `clk.posedge(n=1)` / `clk.negedge(n=1)`：直接定位时钟第 `n` 个有效上升/下降沿（支持门控时钟，免去自己心算纳秒数）；
  - `clk.at(t)` / `sig.at(t)` / `d.point(row_or_name, at=None)`：获取指定行在任意时刻的 `Point`（`at=None` 默认取 `d.now`）。
- 可用 `req in e` 或 `"req" in e` 判断某信号是否在该事件中被赋值。
- **常见误区**：`Event` 只包含**那条语句里真正赋了值的信号**，访问未赋值的信号会抛出明确报错；另外 `d.posedge(clk)` 没加 `(...)` 赋值时返回的是 `Wait` 而不是 `Event`，不能写 `d.posedge(clk).req`。不过 **任意 `Event`、`Point`、`Wait` 或 `clk.posedge(n)` 都可以直接作为 `TimeRef` 传入 `arrow` 二元组、`note`、`region`、`marker`、`measure`、`point`、`sig.at`**，完全不必手动硬编码 `"30ns"`。

### 五类图内标注与底部图注 API

1. **因果箭头 `d.arrow(src, dst, label=None)`**
   - `src` 和 `dst` 支持 `Event` 跳变点（`e1.valid`）、时钟/信号锚点（`clk.posedge(2)`、`sig.at(e2)`），以及 **`(row, TimeRef)` 二元组**（如 `d.arrow(e1.valid, (ready, e3), "握手")`——适合握手沿上某信号保持稳定、没有被再次赋值的场景）；不能是同一个点，也不能跨图表混用。
   - 自动绘制平滑曲线并在中间放置带描边的文字徽章；若遇到总线文本、高亮标题或其他箭头徽章，会自动沿曲线选位或上下错开，并在绘图区左右边界内防裁切。
   - 同一根信号前后两点之间的 `arrow` 会自动画出上拱弧线。

2. **单点气泡注释 `d.note(target, ...)`**
   - 两种调用方式：直接传点句柄（如 `d.note(e.ready, "从机就绪")` 或 `d.note(clk.posedge(3), "采样沿")`），或传信号/时钟与时刻 `d.note(clk, "20ns", "采样沿")`（不画贯穿全图的竖线）。
   - 在锚点位置画出圆点、引线与圆角标签徽章，并自动避让总线文字、区域标题、标记、因果箭头及其他注释；仍存在重叠时会在 `waveview check` 的 `crowded_notes` 中报警。

3. **区间高亮 `d.region(row, start, end, label=None)`**
   - 在指定信号或时钟行 `row`（对象或名字字符串）的 `[start, end]` 区间下方衬一层半透明虚线框，上方放标签；若相邻区域或顶部标记标签重叠，会在 `waveview check` 的 `crowded_regions` 中报警。
   - `start` / `end` 可传任意 `TimeRef`（`Event`、`Point`、`d.now`、`Wait`、时间字符串、数字或 `Fraction`），要求 `0 <= start < end`。

4. **纵向参考线 `d.marker(at=None, label=None)`**
   - 在指定时刻 `at`（不传时默认取 `d.now`）画一条贯穿所有信号直到坐标轴的垂直虚线，上方显示标签胶囊。
   - 多个位置相近、标签会横向碰头的 `marker` 会自动错开到不同高度层，画布顶部也会同步自动增高。

5. **时间间隔测量 `d.measure(start, end, label=None)`**
   - 在波形底部画出双向测量箭头；若不传 `label`，自动填写精准格式化后的耗时（如 `"20ns"`）。
   - 时间区间互相不重叠的多条 `measure` 会智能复用同一水平泳道，节省垂直空白；重叠的 `measure` 会自动分列不同泳道。

6. **底部图注 `d.caption(*texts)`**
   - 在时间轴下方左对齐绘制一条或多条说明文字（传入多行字符串会自动按 `\n` 拆分），类似芯片手册中的 `Note 1 / Note 2`；画布宽度与高度会自动随内容扩展，适合存放较长的协议备注或前提说明。

---

## 7. 主题配色与自定义扩展

内置三套开箱即用主题（`Diagram(theme=...)`）：

| 主题名 | 风格与适用场景 | 核心色彩特点 |
| :--- | :--- | :--- |
| `"light"` | 清爽浅白文档风（默认） | 纯白底 + 柔和浅灰斑马纹，适合嵌入 README、论文、技术 Wiki、打印输出。 |
| `"dark"` | 深邃极客暗夜风 | 深海蓝黑底 + 亮天蓝单线/冰蓝总线，适合深色终端与演示文稿。 |
| `"bamboo"` | **竹林青瓷护眼暗色风** | 墨竹深苔绿背景层次 + 新篁翠竹绿（`#8fb573`）单线 + 青瓷玉色（`#62bdb0`）总线 + 暖象牙白（`#f1e9d2`）标题，柔和耐看且对比清晰。 |

同一 HTML / Markdown 页面中同时内联渲染多种主题的 SVG 时，`waveview` 会自动为每套主题的渐变、斜线纹理和箭头 `<defs>` 加上 `wv-{theme}-` 前缀，彻底避免跨图串色。

### 添加自定义主题

查看 [src/waveview/themes.py](../src/waveview/themes.py) 中的 `THEMES` 字典。每套主题包含完全一致的 **26 个颜色键**：

- 页面与容器：`bg`、`plot_bg`、`row_alt`、`border`、`grid`、`rail`、`badge_bg`
- 文字层级：`title`、`text`、`group`、`muted`
- 波形本体：`clock`、`wave`、`bus`、`bus_text`、`z`、`x`
- 标注类：`region`、`region_text`、`arrow`、`marker`、`marker_bg`、`marker_border`、`marker_text`、`measure`、`note`

只要保持键名集合与 `THEMES["light"]` 一致，即可新增自定义主题并在单元测试中通过调色板校验。

---

## 8. 导出、Notebook 集成与校对工具

### 8.1 保存与导出

```python
d.save("out/axi/write.svg")            # 自动创建缺失的父目录 out/axi/
d.save("out/axi/write.png", scale=3)   # 3 倍分辨率导出 PNG（默认 scale=2）

svg_text = d.to_svg()                  # 获取 SVG 字符串
png_bytes = d.to_png(scale=2)          # 获取 PNG 二进制字节流
```

### 8.2 Notebook 与扩展内联渲染

- **Jupyter / IPython / Marimo**：`Diagram` 实现了 `_repr_svg_()`，在单元格最后一行直接写 `d` 即可直接内联出图。
- **`omp` TUI 渲染插件**（[extensions/waveview.ts](../extensions/waveview.ts)）：通过 `omp install github:zhangwwpeng/waveview`（或本地仓库根目录执行 `omp plugin link .`）并在 `~/.omp/agent/config.yml` 开启 `extensionRenderers.assistantText: true` 后，回复中的 ```` ```python_wave ```` 代码块会在后台运行并自动替换成内联 SVG（支持任意变量名的 `Diagram` 实例，且跨任意工作目录可用；可用 `WAVEVIEW_BIN` 覆盖 CLI 路径）。

### 8.3 文本校对、AI 自检与时序采样

- **`d.describe()`**：生成按时间严格排序的文本剧本，方便 Diff 审阅或交给大模型二次校对时序语义是否符合预期。它会列出标题、时钟参数（含门控窗口与空闲电平）、初值、每一步的状态前后对比 `old→new`（即使使用乱序 `d.at()` 也准确反映变化前值）、`gap` 与全部标注。
- **`d.check()`**：在 `to_svg()` / `to_png()` / `save()` 时自动运行，对两类常见失误发出 `WaveWarning`：
  1. 有事件或标注超出了手动设置的 `end`；
  2. 某信号从未被赋值过且初值为 `"x"`（整行全是未知阴影）。
- **`d.sample(clk, edge="rise", *, include_gaps=False)`**：按时钟有效边沿逐拍采样全部信号，返回 `[{"edge": 1, "time": "5ns", "time_s": 5e-9, "signals": {name: {"sampled": ..., "after": ..., "changed": bool}}}, ...]`，其中 `sampled` 为边沿到达前一瞬间的值（触发器采样值），`after` 为边沿后的更新值。
- **`d.to_dict()` / `d.inspect(fmt="text" | "json")`**：一次输出 `ok`、`svg_valid`、画布尺寸与布局诊断（`truncated_labels` / `dropped_labels` / `crowded_arrows` / `crowded_notes` / `crowded_regions`）、`warnings`、`layout_issues`、`steps` 及各时钟 `clock_samples`，让 AI 无需生成或读取 PNG 图片即可完成版式与时序双重验证。

### 8.4 CLI 命令与 Skill 封装

仓库通过 `pyproject.toml` 注册了 `waveview` CLI（推荐在本地执行一次 `uv tool install -e .` 安装到 `~/.local/bin/waveview` 全局使用；未安装时在仓库内可用 `uv run waveview` 或 `python -m waveview`）：

```bash
waveview check examples/apb.py               # 打印自检摘要 + describe() + @posedge 采样表
waveview check examples/apb.py --format json # 结构化 JSON，供脚本或 AI 解析
waveview check - --strict                    # 从 stdin 读取 Python 或 ```python_wave 块；有警告/拥挤退出码为 2
waveview render examples/apb.py -o out/apb.svg
waveview render examples/apb.py -o -         # 直接向 stdout 输出 SVG（omp 插件即通过该命令调用）
```

- **`waveview` Skill**：完整闭环规范（TUI ```` ```python_wave ```` 画图规则、发前 `waveview check -` 自检清单与 JSON Schema）保存在 [skills/waveview/SKILL.md](../skills/waveview/SKILL.md)。

---

## 9. 典型数字协议实战示例

仓库中的 [examples/](../examples) 目录内置了 6 个可直接运行的实战脚本：

| 文件 | 覆盖场景与亮点 | 运行命令 |
| :--- | :--- | :--- |
| [handshake.py](../examples/handshake.py) | 单时钟 `valid/ready` 握手与跨行因果箭头 | `uv run python examples/handshake.py` |
| [cdc.py](../examples/cdc.py) | `100MHz` 与 `143MHz`（相位 `2ns`）异步双时钟、分组、亚稳态 `region` 与跨时钟测量 | `uv run python examples/cdc.py` |
| [burst.py](../examples/burst.py) | `gap("1us")` 折叠空闲、总线格式化、三态信号与 `dark` 主题 | `uv run python examples/burst.py` |
| [spi.py](../examples/spi.py) | 用 `wire` 手动翻转 `SCLK` 并逐位比较赋值的经典 SPI 写法 | `uv run python examples/spi.py` |
| [gated_spi.py](../examples/gated_spi.py) | **门控时钟** `SCLK`（`enable`/`disable`）+ `warn_noop=False` 的简洁 SPI 写法 | `uv run python examples/gated_spi.py` |
| [apb.py](../examples/apb.py) | 两层嵌套分组、APB 写（含等待周期）与读连续传输（`bamboo` 主题） | `uv run python examples/apb.py` |

### 完整小示例：门控 I2C 起始/停止时序（`idle=1`）

```python
from waveview import Diagram

d = Diagram(title="I2C 起始与首字节（门控 SCL, idle=1）", theme="bamboo", warn_noop=False)
scl = d.clock("scl", period="100ns", phase="25ns", idle=1, gated=True, label="SCL")
sda = d.wire("sda", init=1, label="SDA")

start = d.at("30ns")(sda=0)          # SCL 为高时 SDA 下降 -> START 条件
scl.enable("75ns")                    # 75ns 时从 idle=1 下降，进入时钟节拍

for b in [1, 0, 1, 0, 0, 1, 0, 0]:    # 7-bit 地址 + W(0)
    d.negedge(scl)(sda=b)             # 在 SCL 下降沿切换 SDA

d.negedge(scl)(sda="z")               # 第 9 拍释放 SDA 等从机 ACK
d.negedge(scl)(sda=0)                 # 拉低 SDA 准备释放 STOP
d.posedge(scl)                        # SCL 回到高电平
scl.disable()                         # 关闭时钟，维持 SCL=idle=1
stop = d.delay("25ns")(sda=1)         # SCL 为高时 SDA 上升 -> STOP 条件

d.marker(start, "START")
d.marker(stop, "STOP")
d.save("out/i2c_gated.svg")
```

---

## 10. 常见报错与排查速查表

所有用法问题都会抛出清晰的中文 `WaveError`（继承自 `ValueError`）或 `WaveWarning`：

| 报错 / 现象 | 原因 | 解决方法 |
| :--- | :--- | :--- |
| `名字 '...' 不是合法的 Python 标识符` | `name` 里包含 `[7:0]`、空格、中文或保留字 | `name` 改为合法英文标识符，原名称写进 `label="data[7:0]"` |
| `信号 "xxx" 不存在，你是不是想写 "yyy"？` | 赋值关键字参数拼错，或写了未声明的信号名 | 按提示更正变量名；确认信号先于赋值声明 |
| `d.posedge()/delay()... 需要先调用赋值才能访问信号 .req` | 直接对 `Wait` 对象取 `.req`（忘了加括号赋值） | 改为 `e = d.posedge(clk)(req=1)`，再用 `e.req` |
| `这个事件没有给信号 "xx" 赋值` | 想取某时刻的信号点 `e.xx`，但创建 `e` 的语句里没给 `xx` 赋值 | 从真正给 `xx` 赋值的那条语句返回值里取，或在同一次赋值中一并写入 |
| `信号 "xx" 在 ... 已经赋值过一次了，同一时刻不能赋值两次` | cursor 没推进时连续两次给同一信号赋值 | 在第二次赋值前插入 `posedge`/`negedge`/`delay` 推进时间，或合并为同一次 `set(a=..., b=...)` |
| `总线 "data" 是 8 位，装不下 256` | 给有位宽限制的 `bus` 赋了越界整数或负数 | 调整数值到 `[0, 2^width - 1]`，或调大 `width` / 不限制 `width=None` |
| `门控时钟 "sclk" 后续没有可用的上升沿` | 在 `stop` 或 `disable()` 窗口结束之后还调用了 `posedge`/`negedge` | 确认窗口时间区间足够覆盖循环所需的全部边沿，或先调用 `clk.enable()` |
| `有事件或标注超出了 end=...，超出的部分不会画出来`（`WaveWarning`） | 手动写的 `end=` 比实际事件时刻小 | 调大 `end=` 或干脆去掉 `end` 参数让库自动留出合适尾部空白 |
| `信号 "xx" 在 ... 本来就是 0，这次赋值不会产生变化`（`WaveWarning`） | 给信号赋的值与当前值完全一致 | 确认时序是否有误；若是循环赋值故意重复写入，设 `Diagram(..., warn_noop=False)` 即可 |
