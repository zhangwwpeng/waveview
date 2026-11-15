# waveview 设计文档

> 说明：第 1～16 节保留了 **v0.1 初始设计规格与取舍记录**（供历史参考）；**当前已实现的完整架构、新增功能（门控时钟、`tco`、`clk.posedge(n)` 锚点、`d.note`/`d.caption`、`inspect` / `waveview` CLI、`layout.py` 分层、Skill 与精简后的 `omp` 扩展）见第 17 节。**

## 1. 目标与范围

**一句话**：一个 Python 库，用接近 Verilog testbench 的写法描述数字时序图，输出 SVG / PNG。

**v0.1 做**
- Python API：时钟、单比特信号、总线、按时钟沿推进的事件描述
- 多时钟 / 异步：每个时钟独立设置频率、相位、占空比
- 标注：因果箭头、区域高亮、竖线标记、尺寸标注
- 分组、时间轴断开
- 输出 SVG、PNG，以及文本形式的事件清单（`describe()`）
- 清晰的报错 + 内置合理性检查

**v0.1 不做**（见第 12 节"以后"）
- 任何 AI 协作（skill、DeepSeek 校对、Claude Code mod）
- 读取 VCD / FST 仿真数据
- 状态机图
- JSON 导入导出、自定义 DSL
- 时钟门控 / 变频

## 2. 总体架构

```
用户的 Python 代码
   │  调用 API（第 4~9 节）
   ▼
数据模型（dataclass，时间用 Fraction 精确表示）
   │
   ├──→ describe()  →  事件清单文本
   ▼
布局（时间 → 像素，行高，标签宽度）
   ▼
SVG 字符串（手写拼接，不依赖 SVG 库）
   ▼
PNG（resvg-py）
```

API 只负责往数据模型里写东西；布局和绘制只读数据模型。以后加 JSON 导入导出、其他前端写法，都只和数据模型打交道。

## 3. 时间与单位

- **所有时间、频率、相位都写成带单位的字符串**：`"10ns"`、`"2.5ns"`、`"100MHz"`、`"90deg"`
  - 时间单位：`fs` `ps` `ns` `us` `ms` `s`
  - 频率单位：`Hz` `kHz` `MHz` `GHz`
  - 也接受纯数字，按 `Diagram(timescale=...)` 的单位理解（默认 `ns`）
- **内部用 `fractions.Fraction`（单位：秒）**，所有边沿时间精确计算，不会因为浮点误差累积而偏移
  - 例：143MHz 的第 1000 个上升沿 = `phase + 999 / 143e6`，精确值

## 4. Diagram

```python
from waveview import Diagram

d = Diagram(
    title="握手",          # 可选，画在图的顶部
    timescale="ns",        # 纯数字时间的单位，也是时间轴刻度的单位
    end=None,              # 图的结束时间；None = 自动（最后一个事件之后再留 2 个最快时钟周期）
    theme="light",         # "light" | "dark" | "bamboo"，配色表见 themes.py
)
```

信号在图上**从上到下按声明顺序**排列。

## 5. 时钟

```python
clka = d.clock("clka", freq="100MHz")
clkb = d.clock("clkb", freq="143MHz", phase="2ns")
clkc = d.clock("clkc", period="8ns", phase="90deg", duty=0.3, label="clk_c (125MHz)")
```

| 参数 | 说明 |
|---|---|
| `name` | 名字，必须是合法的 Python 标识符 |
| `freq` / `period` | 二选一，都写或都不写会报错 |
| `phase` | 第一个上升沿的时间。时间（`"2ns"`）或角度（`"90deg"` = 1/4 周期），默认 0 |
| `duty` | 占空比，0 < duty < 1，默认 0.5 |
| `label` | 图上显示的名字，默认等于 `name` |

- 时钟在整张图的时间范围内自动画满
- 第 k 个上升沿：`phase + (k-1) * period`；下降沿：上升沿 + `duty * period`

## 6. 信号

```python
req  = d.wire("req", init=0)
ack  = d.wire("ack")                                   # init 默认 "x"
data = d.bus("data", init="x", width=8, label="data[7:0]")
```

**wire（单比特）**：取值 `0`、`1`、`"x"`（未知）、`"z"`（高阻）。也接受字符串 `"0"` / `"1"`。

**bus（多比特）**：取值为
- 字符串：原样显示，如 `"0xA5"`、`"IDLE"`、`"addr"`
- 整数：按 `fmt` 格式化显示，`fmt` 可选 `"hex"`（默认）、`"dec"`、`"bin"`
- `"x"` / `"z"`

`width` 可选；写了的话，整数值超出位宽会报错。

| 参数 | 说明 |
|---|---|
| `name` | 合法的 Python 标识符，`d.set(name=...)` 时使用 |
| `init` | 时间 0 处的初始值，默认 `"x"` |
| `label` | 图上显示的名字，可以是任意文字（如 `data[7:0]`、`axi.awvalid`） |
| `width`、`fmt` | 仅 bus |

## 7. 事件：等待 + 赋值

这是整个 API 的核心，对应 Verilog testbench 的过程式写法。

### 7.1 当前时间（游标）

- Diagram 内部维护一个**当前时间**，初始位于 0 **之前**（所以时间 0 处的边沿也能被等到）
- 等待语句把当前时间往后推，赋值语句在当前时间产生变化
- **没有被赋值的信号保持原值**

### 7.2 等待

| Verilog | Python | 含义 |
|---|---|---|
| `@(posedge clka)` | `d.posedge(clka)` | 跳到当前时间**之后**的下一个上升沿 |
| `@(negedge clkb)` | `d.negedge(clkb)` | 跳到当前时间之后的下一个下降沿 |
| `repeat(3) @(posedge clka)` | `d.posedge(clka, 3)` | 连续等 3 个上升沿 |
| `#2` | `d.delay("2ns")` | 当前时间往后推 2ns |
| — | `d.at("63ns")` | 跳到绝对时间 63ns（用于异步事件，如复位） |

### 7.3 赋值

```python
d.set(req=1, data="0xA5")
```

等待和赋值可以写在一行（推荐），也可以分开写：

```python
d.posedge(clka)(req=1, data="0xA5")    # 一行：等待语句返回的对象可以直接调用来赋值

d.posedge(clka)                         # 分两行
d.set(req=1, data="0xA5")
```

### 7.4 事件句柄

每次赋值返回一个**事件句柄**，用于标注时引用"某个信号在这一刻的变化"：

```python
e1 = d.posedge(clka)(req=1, data="0xA5")
e2 = d.negedge(clkb)(ack=1)
d.arrow(e1.req, e2.ack, "握手")        # e1.req = "req 在 e1 这一刻的跳变"
```

## 8. 标注

所有标注中的"时间点"都可以是：事件句柄（`e1`）、信号跳变（`e1.req`）、时间字符串（`"40ns"`）、或 `d.now`（当前时间）。

| API | 画出来 |
|---|---|
| `d.arrow(src, dst, label=None)` | 从一个信号跳变指向另一个信号跳变的箭头。`src`、`dst` 必须是信号跳变（`e1.req`） |
| `d.region(signal, start, end, label=None)` | 在某个信号的某段时间上画半透明高亮（如亚稳态窗口） |
| `d.marker(at=None, label=None)` | 贯穿所有信号的竖直虚线。`at` 默认是当前时间 |
| `d.measure(start, end, label=None)` | 两个时间点之间的尺寸标注（双箭头 + 文字），画在时间轴附近 |

## 9. 分组与时间轴断开

**分组**：在 `with` 块里声明的信号属于同一组，左侧画一个带组名的括号。分组可以嵌套。

```python
with d.group("源时钟域"):
    data_a = d.wire("data_a", init=0)
```

**时间轴断开**：`d.gap("10us")` 和 `d.delay("10us")` 一样会推进当前时间，但这段时间在图上压缩成一个 ≈ 断开符号，不按比例画。

## 10. 输出

```python
d.save("wave.svg")                 # 按扩展名决定格式
d.save("wave.png", scale=2)        # PNG 可以设置缩放倍数（高分屏）
svg: str = d.to_svg()
print(d.describe())
```

`describe()` 输出按时间排列的事件清单，每个事件都写明绝对时间和对应的时钟沿：

```
时钟：clka 100MHz phase=0；clkb 143MHz phase=2ns
t=0ns         初始  req=0 ack=x data=x
t=0ns         clka↑#1   req 0→1, data x→0xA5
t=5.497ns     clkb↓#1   ack x→1
t=10ns        clka↑#2   req 1→0, data 0xA5→x
...
标注：arrow req@0ns → ack@5.497ns "握手"
```

## 11. 报错与检查

### 11.1 错误（直接抛异常，信息里要写清楚是哪一行、怎么改）

- 赋值时信号名不存在，给出最接近的名字：`信号 "akc" 不存在，你是不是想写 "ack"？`
- wire 的值不是 0/1/x/z
- bus 的整数值超出 `width`
- 同一时刻对同一个信号赋值两次
- 时钟的 `freq` 和 `period` 同时写或都没写；`duty` 不在 (0, 1) 范围内
- 信号名重复、不是合法的 Python 标识符
- 箭头的端点不是信号跳变

### 11.2 警告（继续出图，但通过 Python 的 `warnings` 提示）

- `d.at()` 让时间往回走
- 事件或标注超出了 `end`
- 赋值没有改变信号的值（比如本来就是 1，又赋值 1）
- 某个声明的信号从来没被赋值过，并且 `init` 是 `"x"`

## 12. 渲染规范（初稿，写代码时再细调）

- 左侧是信号名列，宽度按最长的 label 自动计算；右侧是波形区
- **时间到像素的比例**：自动选择，保证最快时钟的半个周期不少于约 16px；可以手动指定 `Diagram(px_per_ns=...)`
- wire：0 低、1 高；`x` 画成斜线填充的色块；`z` 画成中间的一条线
- bus：六边形 + 中间显示值，跳变处画成斜边；`x` 画成斜线填充
- 时钟：方波
- 底部是时间轴，带刻度和单位
- 中文字体：SVG 里指定字体回退链（如 `PingFang SC, Noto Sans CJK SC, sans-serif`），PNG 渲染时让 resvg 加载系统字体
- 主题：light / dark / bamboo，配色表集中在 `src/waveview/themes.py` 的 `THEMES`；加新主题只需加一个键集合与 light 相同的 dict，名字自动成为合法的 `theme=` 取值

## 13. 技术栈与项目结构

- Python ≥ 3.11，**uv** 管理项目
- 依赖：**resvg-py**（SVG → PNG）；SVG 手写拼接
- 测试：**pytest** + 快照测试（每个示例对应一份期望输出的 SVG，改动时对比差异）

```
waveview/
  pyproject.toml
  src/waveview/
    __init__.py      # 导出 Diagram
    errors.py        # WaveError、WaveWarning
    units.py         # 解析 "10ns" / "100MHz" / "90deg" → Fraction
    model.py         # dataclass：Clock、Signal、Change、Annotation …
    diagram.py       # Diagram API：游标、等待、赋值、标注、检查
    svg.py           # 布局（时间 → 像素、行布局）+ 绘制 SVG
    describe.py      # 事件清单
    png.py           # resvg-py 封装
  examples/          # handshake.py、cdc.py …
  tests/
  docs/design.md
```

## 14. 完整示例

### 14.1 单时钟握手

```python
from waveview import Diagram

d = Diagram(title="valid/ready 握手")
clk   = d.clock("clk", freq="100MHz")
valid = d.wire("valid", init=0)
ready = d.wire("ready", init=0)
data  = d.bus("data", init="x", label="data[7:0]")

d.posedge(clk)
e1 = d.posedge(clk)(valid=1, data="0xA5")
e2 = d.posedge(clk, 2)(ready=1)
d.posedge(clk)(valid=0, ready=0, data="x")

d.arrow(e1.valid, e2.ready, "2 拍后响应")
d.save("handshake.png")
```

### 14.2 跨时钟域（异步）

```python
from waveview import Diagram

d = Diagram(title="CDC 两级同步")
clka = d.clock("clka", freq="100MHz")
clkb = d.clock("clkb", freq="143MHz", phase="2ns")

with d.group("clka 域"):
    din = d.wire("din", init=0)
with d.group("clkb 域"):
    s1 = d.wire("s1", init=0)
    s2 = d.wire("s2", init=0)

e0 = d.posedge(clka, 2)(din=1)
e1 = d.posedge(clkb)(s1="x")
e2 = d.posedge(clkb)(s1=1, s2="x")
e3 = d.posedge(clkb)(s2=1)

d.region(s1, e1, e2, "亚稳态")
d.arrow(e0.din, e1.s1, "采样")
d.measure(e0, e3, "同步延迟")
d.save("cdc.png")
```

## 15. 默认处理的问题（v0.1 实现）

1. 保留 `d.at()`，时间往回走时给警告
2. 事件句柄没有公开的属性和方法，任何信号名都能用 `e1.名字` 访问；`e1["名字"]` 也可以
3. `d.gap()` 按"推进一段时间并压缩显示"处理
4. 自动结束时间 = 最后一个事件之后再留 2 个最快时钟周期
5. 自动缩放会加宽到每个总线值都能完整显示（总宽度上限约 2400px）

## 16. 以后（不在 v0.1）

- JSON 导入导出（数据模型的序列化）
- AI 协作：skill 文档、`describe()` 用于校对、Claude Code mod 内联显示
- 状态机图（独立的模型和渲染器）
- 读取 VCD / FST
- 时钟门控、变频
- 仿 cocotb 的多过程并行写法
- 协议高层封装（`axi_write()` 等）

## 17. 当前进度与最新架构（2026-11-15）

完整使用说明与示例见 [docs/guide.md](guide.md)。

### 当前模块分层
```
src/waveview/
  __init__.py    # 导出 Diagram / Event / Instant / Point / Wait / 错误类型
  errors.py      # 中文 WaveError / WaveWarning
  units.py       # 时间/频率/相位解析 -> 精确 Fraction 秒
  model.py       # 纯数据模型（Clock / Signal / Point / Annotation / Note / Gap / Step）
  diagram.py     # Verilog 风格过程式 DSL：游标、等待、tco、赋值、非推进锚点、标注与检查
  themes.py      # light / dark / bamboo 统一 26 键配色表
  layout.py      # TimeMap、gap 压缩、自适应缩放、lane 排布与 DiagramLayout 计算
  svg.py         # 只读消费 DiagramLayout 与 Canvas，输出独立作用域 <defs> 的 SVG
  png.py         # resvg-py 导出 PNG
  describe.py    # 按真实时间顺序生成的事件剧本
  inspect.py     # 时钟逐拍采样（sampled vs after）与 SVG 布局自检指标
  cli.py         # waveview check / render CLI（含 CommonMark 顶层围栏扫描）
extensions/waveview.ts   # omp 扩展：只专注于将顶层 python_wave / waveview 围栏内联渲染为 SVG
skills/waveview/         # TUI 画图闭环与自检 Skill 规范
```

### 已完成
- v0.1 核心与快照测试：`tests/snapshots/`，改了渲染后用 `UPDATE_SNAPSHOTS=1 uv run pytest` 更新
- 主题与渲染优化：新增高对比护眼主题 `bamboo`；跨 SVG `<defs>` ID 隔离、`wire` `0↔x` 隔离、跨 `gap` 总线缩放、斑马纹避让分组括号、`Measure` 非重叠泳道复用、`Marker` 错层与箭头/注释防重叠排布
- 时钟与硬件语义增强：门控时钟（`start`/`stop`/`gated`/`idle` 与 `clk.enable()`/`clk.disable()`）；统一 `Point` 支持 `clk.posedge(n)` / `clk.negedge(n)` / `clk.at()` / `sig.at()` / `d.point()`，打通时钟沿到信号跳变的因果箭头与单点注释；新增时钟/单拍 `tco` 输出延迟参数；新增单点引线注释 `d.note(...)` 与底部图注 `d.caption(...)`
- 自检与多端集成：`waveview check / render` CLI（CommonMark 顶层围栏安全解析）、`d.sample()` / `d.to_dict()` / `d.inspect()`、`save()` 自动建目录、`Notebook` `_repr_svg_()` 与 [extensions/waveview.ts](../extensions/waveview.ts) 内联渲染
- 仓库工程化：根目录 MIT [LICENSE](../LICENSE)、GitHub Actions CI（Linux + macOS 跑 `pytest` + `--strict` 自检）与 [skills/waveview/SKILL.md](../skills/waveview/SKILL.md)

### 历史已知问题解决情况
1. ~~不支持门控时钟~~ → 已解决：见 [examples/gated_spi.py](../examples/gated_spi.py)
2. ~~"赋值没有变化"的警告在循环里太吵~~ → 已解决：`Diagram(..., warn_noop=False)`
3. ~~箭头文字可能和区域高亮的文字重叠~~ → 已解决：自动与区域标签、总线文字和既有箭头/注释避让
4. ~~`d.arrow()` 不能从时钟沿出发，标注时钟沿要手算纳秒数~~ → 已解决：支持 `clk.posedge(n)` / `clk.negedge(n)`、`d.point(...)` 与 `tco=`

### 下一步
- 更多长期规划（JSON 导入导出、状态机图、VCD/FST 读取、多过程并行、协议高层封装）见第 16 节。
