# omp-waveview

将 Oh My Pi（`omp`）助手回复中的 `` `python_wave` `` / `` `waveview` `` 代码块在 TUI 中实时渲染为内联 SVG 波形图（同时支持 `` `img` `` / `` `image` `` 围栏内联显示本地图片）。

> 只修改 TUI 显示层，不改动模型上下文与对话记录；建议配合 `waveview` Skill（`waveview check - --strict`）在发出回复前完成自检。

## 三步配置（任意目录通用）

### 1. 安装全局 `waveview` CLI（到 `~/.local/bin/waveview`）

插件通过调用 `waveview render - -o -` 渲染 SVG，与工作目录和具体仓库位置解耦：

```bash
# 本地源码仓库可编辑安装（推荐）
uv tool install -e /path/to/waveview

# 或在新机器直接从 Git 安装
uv tool install git+https://github.com/zhangwwpeng/waveview
```

### 2. 链接或安装 `omp` 插件

在 `waveview` 仓库根目录执行本地插件链接（写入 `~/.omp/plugins` 后可在任意项目目录生效）：

```bash
omp plugin link ./plugins/omp-waveview
```

若仅临时试用单次会话，也可通过命令行直接挂载扩展入口：

```bash
omp -e ./plugins/omp-waveview/index.ts
```

### 3. 开启扩展回复渲染

`omp` 默认关闭扩展接管助手文本渲染，请在全局配置 `~/.omp/agent/config.yml`（或项目 `.omp/config.yml`）中启用：

```yaml
extensionRenderers:
  assistantText: true
```

也可以在 `omp` TUI 菜单中切换：**Tools → Extensions → Extension Reply Renderers**。

## CLI 解析顺序与环境变量

`renderWaveBlock()` 会按以下优先级定位 `waveview` 可执行文件并执行 `waveview render - -o -`：

1. `WAVEVIEW_BIN` 环境变量指定的 `waveview` 路径；
2. `PATH` 中的 `waveview`；
3. `~/.local/bin/waveview`（`uv tool install` 默认位置）；
4. 若插件是从本机仓库目录软链接过来的，兜底回退到仓库 `.venv` 或 `uv run --project <repoRoot> waveview`。

如从独立副本加载插件且仅设置了自定义路径，可显式导出：

```bash
export WAVEVIEW_BIN=/path/to/waveview
export WAVEVIEW_ROOT=/path/to/waveview
```
