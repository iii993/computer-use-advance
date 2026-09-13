![Krita × MCP](docs/banner.png)

# Krita MCP

An MCP server that lets Claude drive [Krita](https://krita.org): create
documents, build layer stacks, paint shapes and text, run filters, export
files — and *look at the canvas it just made*.

The banner above was painted by Claude through this bridge. So was
[the example poster](docs/example-poster.png). No hand-editing, no image
libraries — just tool calls.

<p align="center">
  <img src="docs/example-poster.png" alt="A sunset landscape painted in Krita through the MCP" width="700">
</p>

---


## 本项目（绘画 MCP）新增功能

本项目基于 CC0 授权的 Krita × MCP 原型搭建，并**新增一层专注“绘画”的 MCP 工具**，让 LLM 直接用 Krita 的 Python API 绘制：

**基础功能**
- `create_canvas` — 创建画布（Krita createDocument）
- `get_canvas_content` — 读取画布内容，渲染回 PNG 供观察
- `draw_point` — 画点
- `draw_line` — 画直线
- `draw_rect` / `draw_ellipse` / `draw_polygon` — 简单几何图形
- `set_brush` — 切换画笔纹理（solid / dashed / dotted）

**高级功能**
- `draw_smooth_path` — 根据控制点用 Catmull-Rom 样条平滑连线
- `draw_stroke` — 模拟压感笔触（按压力曲线动态控制线宽/不透明度）


**新增能力(批量 / 画笔 / 图层 / 橡皮擦 / 变形 / 液化)**
- `run_paint_actions` — 批量执行多个工具调用(一次完成一组操作)
- `set_brush` — 扩展支持 种类(`preset`)、颜色(`color`)、力度(`flow` 0~1, 影响透明度)、大小(`size`/`width`)、不透明度(`opacity`), 以及 `layer` 切换到目标图层后绘制
- `erase` — 橡皮擦: 把图层区域填充为指定颜色(默认白色, 而非透明)
- `liquify` — 变形画笔: 沿 stroke 圆盘做局部膨胀/收缩几何扭曲
- `smudge` — 液化/涂抹画笔: 把笔画区域颜色向局部均值混合模糊



**曲线与压感(实现要点)**
- `draw_smooth_path` — 用 **Catmull-Rom 三次样条**对控制点插值: 对每 4 个相邻点, 中间段在 t=0 过 P1、t=1 过 P2, 逐段拼接即得一条**每个点都穿过**的光滑曲线; 再细分采样成折线一次描边。
- `draw_stroke` — 压力映射为**线宽 + 不透明度**: 线宽 = min_width + (base_width − min_width)·p, 不透明度 = 0.30 + 0.70·p, 沿折线采一圈圈实心圆重叠成渐变笔触; 检测到 Krita 6.0 的 `node.paintLine` 时改走真实画笔+真实压力。
- **三维点 [x, y, 压力]**: 当前接口压力走独立 `pressures` 数组; 可升级为每点自带压力 `[[x,y,p],...]`, 在参数解析处做维度解包即可兼容两种输入(详情见 `docs/功能详解-README.md` §4.3)。

装好插件并启动 Krita 后，用 `python mcp_server.py --selftest` 验证连通，即可通过这些工具绘图并回看结果。


## Why it's useful

Claude can't see what it's doing in most creative tools. Here it can:
`get_image` renders the canvas and hands back a PNG, so Claude draws, looks at
the result, notices the gradient banded or the reflection looks like a
staircase, and fixes it. That loop is the whole point.

It works on real Krita documents — your layers, your colour profiles, your
`.kra` files — not a sandbox.

## Requirements

- **Krita 5.2+** (developed and tested against 5.3.3)
- **Python 3.8+** on your PATH, for the MCP server
- An MCP client — Claude Code, Claude Desktop, or anything else that speaks MCP

The MCP server uses **only the Python standard library**. There is nothing to
`pip install` and no virtualenv to manage.

## Install

### 1. Install the Krita plugin

Close Krita first — it rewrites its config on exit and would undo the enable
flag.

**Windows**

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

**Linux / macOS**

```bash
./install.sh
```

The script copies the plugin into Krita's `pykrita` folder and sets
`enable_krita_mcp=true` in `kritarc`.

Prefer to do it by hand? Copy `pykrita/krita_mcp/` and
`pykrita/krita_mcp.desktop` into Krita's resource folder under `pykrita/`, then
enable **MCP Bridge** in *Settings → Configure Krita → Python Plugin Manager*.

### 2. Start Krita and check it

```bash
python mcp_server.py --selftest
```

```
health: Krita 5.3.3, plugin 1.0.0, 31 operations
  [ok] krita_instance         5.3.3
  [ok] create_document        64x48
  [ok] pixel_channel_order    read back r=255 g=0 b=0 on a little-endian host
  [ok] pixel_roundtrip        read #0080ff
  [ok] draw                   pixel at 10,10 is #ff0000
  ...
9/9 checks passed
```

Inside Krita, *Tools → Scripts* now has **Start/Stop MCP Bridge** and
**MCP Bridge Status…**.

### 3. Connect your MCP client

**Claude Code**

```bash
claude mcp add krita -- python /absolute/path/to/mcp_server.py
```

**Claude Desktop** — add to `claude_desktop_config.json`
(`%APPDATA%\Claude\` on Windows, `~/Library/Application Support/Claude/` on
macOS):

```json
{
  "mcpServers": {
    "krita": {
      "command": "python",
      "args": ["/absolute/path/to/mcp_server.py"]
    }
  }
}
```

Restart the client. Krita must be running for the tools to do anything.

## Try it

> Make a 1200×800 poster in Krita: a sunset over mountains reflected in water,
> on separate layers. Show me the result when you're done.

> Open `photo.jpg`, add a 40% opacity black layer, and put the title
> "SUMMER 1998" in the bottom left. Export it as `poster.png`.

> What layers are in my open document? Set the top one to multiply at 60%.

## Tools

| Tool | What it does |
| --- | --- |
| `status` | Krita version and every open document |
| `inspect_document` | Canvas, colour space, full layer tree, selection |
| `create_document` / `open_document` | Make or load a document |
| `save_document` / `export_document` | Save in place or save-as; export to PNG/JPEG/etc. |
| `close_document` | Close a document (see *Known issue*) |
| `transform_image` | resize canvas, scale, rotate, crop, flatten |
| `create_layer` / `set_layer` / `delete_layer` | Layer stack management |
| `duplicate_layer` / `move_layer` / `merge_layer_down` | Restacking and merging |
| `get_image` | Render the canvas and return a PNG Claude can see |
| `get_pixel` | Exact colour at one pixel |
| `draw` | Shapes, text, gradients, bitmaps onto a paint layer |
| `apply_filter` | Any Krita filter, whole layer or a region |
| `set_selection` | rect / all / none / invert / grow / shrink / feather |
| `list_capabilities` | Filter names and parameters, blending mode ids |
| `trigger_action` | Fire a Krita menu action by id (`edit_undo`, …) |
| `run_python` | Arbitrary Python inside Krita, full libkis access |
| `self_test` | End-to-end health check |

### Drawing

`draw` takes a list of commands rendered in order onto an RGBA/8-bit paint
layer: `fill_rect`, `rect` (optional corner radius), `ellipse`, `circle`,
`line`, `polyline`, `polygon`, `text`, `linear_gradient`, `radial_gradient`,
`image` (paste a base64 PNG), and `clear` (erase to transparency). Colours
accept `#rrggbb`, `#rrggbbaa`, SVG names, or `[r,g,b,a]`.

Geometry uses `w`/`h`; outline thickness is `stroke_width`, kept separate so
the two can't be confused. Only the union of affected pixels is read, painted
and written back, so a small shape on a huge canvas stays cheap.

### Addressing layers

Layers are reported in docker order, top first. Reference one by name
(`"Sky"`), path through groups (`"Background/Sky"`), index path (`"#0/#1"`), or
`"uuid:…"`. Omit it for the active layer. Listings return all of these, so
there's always an unambiguous way to name a layer.

## How it works

```
MCP client  ──stdio JSON-RPC──▶  mcp_server.py  ──HTTP──▶  Krita plugin  ──▶  libkis
                                  (stdlib only)   loopback   (GUI thread)
```

Krita only runs Python *inside* itself, and MCP servers are separate processes,
so there are two halves. They find each other through a small file in Krita's
data folder holding the port and a random per-session token. The bridge binds
loopback only and requires that token, so a web page can't drive your Krita.

**The GUI-thread rule.** libkis isn't thread-safe. The HTTP server answers on
worker threads, so every Krita call is marshalled to Krita's GUI thread through
a queued Qt signal and waited on with a deadline. If Krita's UI is blocked — a
modal dialog, a long filter — you get a clean `krita_busy` error at the
deadline instead of a hung tool call. **Requests never hang.**

## Things worth knowing

**Drawing bypasses undo.** `draw` writes pixels directly, which Krita's undo
history doesn't record. Draw onto a layer you can delete. `apply_filter` on a
whole layer *is* undoable; on a sub-region it isn't, and the response says
which you got.

**Blending modes can't be validated.** Krita 5.3 has no API to enumerate
composite ops and accepts any string, silently rendering unknown ids as Normal.
`set_layer` canonicalises the ids it knows and warns on anything else, so a typo
is visible rather than silent.

**Direct pixel access needs RGBA/8-bit.** `draw`, `get_pixel` and per-layer
`get_image` refuse other colour spaces with a clear message. The merged
`get_image` works regardless.

## Known issue: closing documents can crash Krita

`close_document` occasionally takes Krita down — roughly one close in five at
the end of a long editing session. A freshly created or lightly edited document
closes reliably.

The fault is inside Krita's own C++ teardown, after `Document.close()` has
returned. It reproduces identically through Krita's own *File → Close*, so it
isn't caused by using libkis and isn't something a plugin can prevent. Tried and
ruled out: draining the image scheduler, disabling autosave, clearing the
modified flag, forcing a GC, pumping the event loop, closing views first, and
saving before closing — that last one is the intuitive fix and it doesn't help.

**Prefer leaving documents open and closing them yourself in Krita.** The tool
description tells Claude the same. If Krita does die, nothing hangs: the next
call returns a clear "could not reach Krita" message.

## Troubleshooting

**"Could not reach the Krita MCP bridge"** — Krita isn't running, or the plugin
isn't enabled. Check *Tools → Scripts → MCP Bridge Status…*. If the menu entries
are missing, the plugin didn't load: enable it in the Python Plugin Manager and
restart Krita.

**菜单里没有 MCP Bridge(插件没被加载)** — 2026-09-13 实机踩坑记录,照顺序排查:

1. **先看插件的启动落盘日志** `%APPDATA%\krita\krita_mcp_boot.log`:
   - 文件不存在 → Krita 压根没 import 这个插件,继续第 2、3 步
   - 出现 `bridge start FAILED: …` → 看 traceback(一般是端口/权限)
   - 出现 `bridge started on port …` → 插件其实已经好了,问题在客户端那一侧
   > Krita 是无控制台的 GUI 进程,`print` 和 traceback 会被直接丢掉,所以**不要看终端**,看这个文件。
2. **确认 `enable_krita_mcp=true` 写在 `%LOCALAPPDATA%\kritarc`**。Krita 5.3 在 Windows 上读的是**这个**文件(不是 `%APPDATA%\krita\kritarc` —— 那个位置是干扰项,放那儿没用)。`install.ps1` 写的正是正确的那个。
3. **`kritarc` 损坏会让插件静默不加载** ← 本次真凶。症状:Krita 启动要 20~90 秒、进程 `Responding=False`、`krita.log` 走到 `foreign_keys state: 0 -> 1` 之后停住,插件永远不加载,而 `boot.log` 始终为空。
   修复(**会丢失 Krita 的窗口布局/快捷键等个人设置,先备份**):

   ```powershell
   Copy-Item "$env:LOCALAPPDATA\kritarc" "$env:LOCALAPPDATA\kritarc.bak"
   Rename-Item "$env:LOCALAPPDATA\kritarc" 'kritarc.quarantine'
   # 启动一次 Krita 让它重建配置, 再在新 kritarc 里写入(或直接重跑 install.ps1):
   #   [python]
   #   enable_krita_mcp=true
   ```

   修复后 `boot.log` 立刻出现完整链路,`9797` 端口进入 Listen。
4. **顺带**:`%APPDATA%\krita\resourcecache.sqlite`(几十 MB)只是资源缩略图缓存,启动异常时可以移走让它重建,不影响画作与笔刷。

**Tools don't appear in the client** — restart it after editing the config, and
make sure the path to `mcp_server.py` is absolute.

**Port already in use** — the bridge scans upward from 9797 and writes whichever
port it got into the discovery file, so this normally resolves itself. Set a
different starting port with `port=` under `[krita_mcp]` in `kritarc`.

**Something crashed and you want to know why** — set `KRITA_MCP_TRACE=1` before
starting Krita to log every operation to `krita_mcp_trace.log` in Krita's data
folder, flushed per line. Krita is a GUI process with no console, so a trace
entry with no matching completion names the operation that killed it.

## Testing

```bash
python mcp_server.py --selftest    # quick health check inside Krita
python test_mcp.py                 # full suite, 77 checks
python test_mcp.py -v              # ...printing every tool result
python example_poster.py           # paint the example poster
python example_banner.py           # paint this README's banner
```

`test_mcp.py` spawns `mcp_server.py` and speaks real JSON-RPC to it, so a pass
covers the whole chain: protocol handshake, tool schemas, every tool against a
live Krita, pixel-exact drawing results, and the error paths. It cleans up after
itself.

Diagnostics:

```bash
python mcp_server.py --list-tools
python mcp_server.py --call status
python mcp_server.py --call draw --params-file commands.json
```

## Development

```powershell
powershell -ExecutionPolicy Bypass -File dev_reload.ps1
```

Copies the plugin sources over and hot-reloads `krita_mcp.ops` inside the
running Krita, so operations can be edited without restarting it. Changes to
`extension.py`, `httpserver.py` or `mainthread.py` still need a restart — those
objects already exist.

### Layout

```
mcp_server.py              MCP server (stdlib only) + CLI
install.ps1 / install.sh   install and enable the plugin
dev_reload.ps1             hot-reload during development
test_mcp.py                end-to-end suite over real JSON-RPC
stress_close.py            soak test for the close path
example_poster.py          worked example, and the client the examples share
example_banner.py          paints this README's banner
pykrita/
  krita_mcp.desktop        plugin manifest
  krita_mcp/
    __init__.py            registers the extension
    extension.py           lifetime, menu actions, settings
    httpserver.py          loopback HTTP, auth, discovery file, tracing
    mainthread.py          worker thread -> GUI thread hand-off
    ops.py                 the operations
    imaging.py             QImage <-> Krita pixels, colour parsing
```

### Configuration

Krita reads these from `kritarc` under `[krita_mcp]`:

| Key | Default | Meaning |
| --- | --- | --- |
| `autostart` | `true` | Start the bridge when Krita starts |
| `port` | `9797` | First port to try; scans upward if taken |

Environment variables, read by both halves:

| Variable | Meaning |
| --- | --- |
| `KRITA_MCP_STATE_DIR` | Override where the discovery file lives |
| `KRITA_MCP_TRACE` | Set to `1` before starting Krita to log every operation |
| `KRITA_MCP_PORT` / `KRITA_MCP_TOKEN` | Skip discovery and connect directly |

## Platform support

Developed and tested on **Windows 10 with Krita 5.3.3**. The code paths for
Linux and macOS are implemented — resource folders, config locations, the
installer — but have not been run there. Reports welcome.

## License

[CC0 1.0 Universal](LICENSE) — public domain. Do whatever you like with it.
