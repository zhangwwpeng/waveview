# waveview

## 画图与自检（```python_wave 块 / Skill）

- **封装好的 Skill**：`skills/waveview/SKILL.md` 包含完整的 TUI 画图闭环、API 速查与时序识别规范（通过 `magpie` 管理链接）。
- **先自检再发送**：每个 ```` ```python_wave ```` 块在写进回复之前，优先用 `waveview check -`（已通过 `uv tool install -e .` 装到 `~/.local/bin` 后可直接在任意目录调用；在未全局安装环境的仓库内用 `uv run waveview check -`；可用 `--strict` 让 warning/拥挤直接失败）检查：
  - 确认退出码 0、`[waveview check] OK`、`svg=valid`；
  - 确认无 `truncated_labels` / `dropped_labels` / `crowded_arrows` / `crowded_notes` / `crowded_regions`；
  - 对照输出的 `@posedge` 时钟边沿采样表与 `describe()` 剧本确认时序语义无误（需要严格验证基础 SVG 输出时也可运行 `uv run python -`）。
- **禁止靠读 PNG 验图**：不要自己生成 `.png` 再用图片读取检查波形，`waveview check` 的布局统计与时钟采样表已涵盖全部几何与时序信息。
- **注释与时序尽量全画进图里**：`extensions/waveview.ts` 会将整个围栏替换为 SVG，Python `#` 注释不会显示给用户；任何注释一律用库函数（`d.note` / `d.caption` / `d.arrow` / `d.region` / `d.marker` / `d.measure`），不要手写注释或额外凑图，也不要在回复正文里重复展开"哪个信号在某拍怎么样"的时序流水账——能体现在图里的都直接画进图里。
- 块里不要自己 `print(d.to_svg())` 或 `d.save(...)`：`extensions/waveview.ts` 插件会自动补上输出，并把围栏换成 ```` ```svg ```` 内联显示。
- 报错不会回到模型上下文（renderer 只改显示），本地验证是唯一闭环。用户贴的报错才算数。
- 时钟 `phase=0` 首沿规则：`d.clock(freq="100MHz")` 首拍上升沿 `#1` 在 `t=0ns`（`0 / 10 / 20ns...`，非 `5 / 15 / 25ns`），且首个 `d.posedge(clk)` 直接落 `0ns`；标握手时刻时用 `d.note(clk, e_hs, ...)` / `d.note(ready, e_hs, ...)` / `d.marker(e_hs, ...)`，勿钉在同拍 `1→0` 撤销的 `valid` 下降沿上。
- 信号名以声明为准：`d.wire("valid")` 之后只能写 `valid=`，不能写 `v=`。
- 事件句柄只带该事件真正赋过值的信号：`e.we` 在没给 `we` 赋值的事件上会抛 `WaveError`；指向同拍保持不变的信号可用 `(row, e)` 二元组、`(row, clk.posedge(n))` 或 `d.point(row, e)`（`Event`/`Wait`/`Point` 均可直接作 `TimeRef`，无需硬编码时间）。

## 开发与 CLI

```bash
uv run waveview check examples/apb.py               # 渲染自检 + describe() + 时钟采样表
uv run waveview check examples/apb.py --format json # 结构化 JSON，供脚本/AI 识别时序
uv run waveview render examples/apb.py -o out/apb.svg
uv run python examples/cdc.py                       # 输出到 out/
uv run pytest                                       # 改了渲染后用 UPDATE_SNAPSHOTS=1 更新快照
```
