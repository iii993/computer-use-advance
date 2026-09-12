# 电脑操控 MCP 接口文档

> **适用代码**: `computer_mcp/server.py`(server v3.0.0)、`computer_core/`、`input_engine/`、`ai_mode/`
> **最近更新**: 2026-09-11
> **读者**: ① 通过 MCP 调用本插件的 AI / 客户端开发者;② 要给本插件加新功能的人
> **配套文档**: 设计、实测与实施记录见 `docs/superpowers/plans/2026-09-11-control-refactor-and-uia-observer.md`;用户手册见根目录 `README.md`

---

## 1. 这是什么

Windows 上的"电脑操控"服务,以 MCP(JSON-RPC 2.0 over stdio)暴露 **26 个工具**,覆盖四类能力:

| 类别 | 工具 | 说明 |
|---|---|---|
| 观察 | `screenshot` `zoom` `zoom_to_screen` `list_windows` `get_state` | 截图 / 10X 放大镜 / 放大图像素反算 / UIA 窗口清单 / 状态 |
| 鼠标 | `click` `move` `drag` `mouse_down` `mouse_up` `scroll` `slide` | 点击(含长按/侧键/连击/坐标序列)、纯移动、拖拽、滚轮、相对滑动 |
| 键盘 | `type_text` `send_text` `press_key` `key_down` `key_up` `combo` | 输入文字、输入并提交、按键、组合键 |
| 窗口/会话 | `focus_window` `switch_mode` `set_config` `set_brush` `draw_curve` `check_vision` `wait` `run_actions` | 置前窗口、切模式、改配置、绘画、批量执行 |

其中 `focus_window` / `send_text` / `move` / `zoom` / `zoom_to_screen` / `list_windows` 是"高层工具",专门用来消除 AI 常见的两类翻车:**坐标算错**与**不知道目标窗口在哪 / 发送快捷键是什么**。

---

## 2. 启动与接入

```bash
python H:\cu-a\computer_mcp\server.py
```

- 传输: stdio,每行一条 JSON-RPC 消息;**stdout 只输出协议**,日志走 stderr 与 `logs/`
- 依赖: Windows 10/11 + Python 3.10+;第三方依赖(pynput / Pillow / pystray / keyboard)放在项目 `vendor/`,`server.py` 启动时自动加入 `sys.path`
- **进程必须常驻**: 坐标口径(`img_size`)与放大镜状态(`zoom` 的最近一次映射)都存在**进程内存**里。若客户端每次调用都新起一个 Python 进程,则每次使用 `image` 口径前都必须先 `screenshot`(见 §3.4)
- UIA 会话是 COM STA,必须在创建它的线程里使用;`server.py` 单线程读取 stdin,天然满足

最小交互示例:

```jsonc
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_state","arguments":{}}}
```

### 2.1 挂载到 DSH(已完成,2026-09-12)

`H:\dsh-home\profiles\web\cordis.patch.yml` 里有两个 insert 块:

```yaml
- insert:
    - id: mcp-computer
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: computer
        transport: stdio
        command: 'H:\PY\python.exe'          # 必须绝对路径: GUI 进程 PATH 里未必有 python
        args: ['H:\cu-a\computer_mcp\server.py']
        env:
          PYTHONIOENCODING: 'utf-8'            # 见下方"编码坑"
```

`mcp-krita` 同构,args 指向 `H:\cu-a\krita_mcp\mcp_server.py`。

- **热加载**: 保存该文件即生效(cordis HMR),**不需要重启 DSH web**。实测约 8 秒后新的 `python.exe … server.py` 子进程起来、工具列表刷新;核对方式:
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -like '*computer_mcp*'`
- 工具前缀: `mcp__computer__*`、`mcp__krita__*`

### 2.2 编码坑(必看,踩过)

Windows 下 Python 的 stdio 默认走 **locale 编码(GBK)**,而 MCP 客户端按 **UTF-8** 收发。不在 `main()` 里统一,后果是:**工具描述在宿主侧全是乱码**、中文参数(`send_text` 的中文)与中文返回值也会坏。

```python
sys.stdin.reconfigure(encoding="utf-8", errors="replace")
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
```

`computer_mcp/server.py` 已修;profile 里再给 `PYTHONIOENCODING=utf-8` 做双保险。**自己写新 MCP server 时务必照做**(可参考 `krita_mcp/mcp_server.py` 的 `main()`)。

---

## 3. 坐标系契约(必读,踩过坑)

### 3.1 两种口径

| `coord` | 入参含义 | server 行为 |
|---|---|---|
| `image`(**默认**) | **最近一次 `screenshot` 返回的图像内像素** | 按 `img_size → screen_size` 线性换算成屏幕坐标 |
| `screen` | 屏幕物理像素 | 原样使用,不换算 |

### 3.2 为什么需要它

截图会被等比缩到约 64 万像素(例如 1920×1080 → 1066×600,比例 ≈ 0.555)。模型从图里读到的坐标是**图内像素**,而鼠标 API 要的是**屏幕像素**。两者混用会**静默偏位**(差 1.8 倍),而且不会报错 —— 实测就发生过"以为是输入框、实际点到正文"的事故。

### 3.3 每个坐标类工具都会回显口径

```jsonc
{"count":1, "points":[[1080.67, 948.6]], "coord":"image",
 "img_size":[1066,600], "screen_size":[1920,1080]}
```

- `points` 是**换算后的屏幕坐标**,可以直接核对"我到底点在哪"
- `get_state` 也会返回一个 `coord` 块,随时可查当前口径
- 若返回值里的 `img_size` 和你手上那张图不一致,说明你读的是旧图,先重新 `screenshot`

### 3.4 未截图时

`img_size == screen_size`(1:1),两种口径等价。所以"刚启动、还没截图"时按屏幕坐标点击是安全的;但一旦截过图,`image` 口径就变成图内像素。

### 3.5 推荐用法

1. **常规**: 先 `screenshot` → 之后一路用默认 `image` 口径
2. **手上已有屏幕坐标**: 传 `coord:"screen"`,最不容易错
3. **要看小目标**: `screenshot`(粗看) → `zoom`(精看) → `zoom_to_screen`(换算) → 用返回的 `img_x/img_y` 调 `click` / `move`
4. **非驻留场景**: 每次用 `image` 口径前都先 `screenshot`

---

## 4. 工具总表

| # | 工具 | 一句话 | 坐标类 |
|---|---|---|---|
| 1 | `screenshot` | 截全屏返回图像,并定义 `image` 口径 | — |
| 2 | `get_state` | 模式 / 光标 / 画笔 / 当前口径块 | — |
| 3 | `click` | 点击(原地 / 定位 / 长按 / 侧键 / 连击 / 坐标序列) | ✔ |
| 4 | `move` | 只移动不点击(单点或序列,可设耗时与步间间隔) | ✔ |
| 5 | `drag` | 按住左键拖拽 | ✔ |
| 6 | `mouse_down` | 在 (x,y) 按下并保持 | ✔ |
| 7 | `mouse_up` | 松开鼠标键 | ✔ |
| 8 | `scroll` | 移到 (x,y) 后滚轮 | ✔ |
| 9 | `slide` | 相对滑动(防检测) | ✔ |
| 10 | `type_text` | 向聚焦控件输入文字 | — |
| 11 | `send_text` | **输入并按组合键提交**(如 ctrl+enter) | — |
| 12 | `press_key` | 敲一次按键 | — |
| 13 | `key_down` | 按住按键 | — |
| 14 | `key_up` | 释放按键 | — |
| 15 | `combo` | 组合键 | — |
| 16 | `wait` | 等待若干秒 | — |
| 17 | `set_brush` | 设画笔大小(1~200) | — |
| 18 | `draw_curve` | 按控制点画平滑曲线(可注入压力) | ✔ |
| 19 | `switch_mode` | 切 game/draw/work 模式 | — |
| 20 | `set_config` | 改白名单配置 | — |
| 21 | `check_vision` | 检测配置的模型是否支持识图 | — |
| 22 | `list_windows` | UIA 窗口清单(文本) | — |
| 23 | `focus_window` | **把窗口激活到前台** | — |
| 24 | `zoom` | 10X 放大镜(返回图像 + 映射元数据) | ✔ |
| 25 | `zoom_to_screen` | 放大图像素 → 可用坐标 | — |
| 26 | `run_actions` | 批量按顺序执行多个动作 | 视动作而定 |

---

## 5. 工具详解

> 约定: "坐标类"工具都支持 `coord`(默认 `image`),返回值都回显 `coord / img_size / screen_size`。
> 所有 `number[][]` 形式的点都支持二维 `[x,y]` 或三维 `[x,y,t]`(`t` 是该点自己的耗时/按压时长,覆盖函数级参数)。

### 5.1 观察类

#### `screenshot`
截取整个屏幕并返回图像(等比缩到约 64 万像素)。**它同时定义 `image` 口径**:返回值里的 `img_size` 就是这张图的尺寸。

- 参数: 无
- 返回: 图像 + 文本,文本含 `{"img_size":[w,h],"screen_size":[W,H],"coord":"image","hint":...}`
- 坑: 图是缩放过的,**不要**自己拿图去推屏幕像素,交给 server 换算;坐标类工具默认按这张图的像素解释

#### `get_state`
读当前状态,并确认"现在坐标按哪种口径解释"。

- 参数: 无
- 返回: `{"mode":"work|game|draw","cursor":[x,y](image 口径),"brush_size":n,"pen_channel":bool,"coord":{...}}`

#### `zoom` (坐标类)
10X 放大镜:把 (x,y) 周围一小块屏幕用 **NEAREST**(像素复制,不插值)放大后返回图像,用来看清小字/小图标/细线。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| x, y | number | 鼠标位置 | 观察中心(默认 `image` 口径) |
| coord | `"image"\|"screen"` | `"image"` | 坐标口径 |
| factor | int | 10 | 放大倍率;输出边长超过 1200 会自动下调 |
| src | int | 100 | 取样边长(屏幕像素) |

- 返回: 图像 + `zoom_meta`: `{seq, factor, out_size, screen_rect(屏幕矩形), img_rect(image 口径矩形), coord, img_size, screen_size}`
- 关键: **倍率与视野成反比** —— 10X 且上限 1200 时只覆盖屏幕约 120×120;要覆盖更大范围就降 factor 或加 src
- 坑: 放大图像素 ≠ 屏幕像素;别心算,交给 `zoom_to_screen`

#### `zoom_to_screen`
把**最近一次 `zoom` 图像**里的像素 (px,py)(图左上角为 0,0)换算成可用坐标。

| 参数 | 类型 | 说明 |
|---|---|---|
| px, py | number | 放大图内的像素 |

- 返回: `{screen_x, screen_y, img_x, img_y, img_size, seq, factor, coord, screen_size, note}`
- 用法: `click` / `move` 直接用返回的 **`img_x/img_y`**(它们就是 `image` 口径)
- 坑: ①本工具**不重新截图**,画面变了要重新 `zoom`;②从未 `zoom` 过 → `RuntimeError`(明确报错,不猜);③像素越界 → `ValueError`(错误信息带真实 `out_size`)

#### `list_windows`
用 UIA 无障碍树列出当前可见顶层窗口,返回文本清单:`[序号] hwnd=... type=Window rect=(l,t,r,b) class='...' name='...'`。

- 参数: 无
- 用途: 不截图也知道有哪些窗口、在哪(`rect` 是屏幕坐标!)、句柄是多少(`hwnd` 可直接给 `focus_window`)
- 坑: UIA 只对支持无障碍接口的程序有效(Win32 / WPF / WinForms / UWP / 大体上 Electron);**游戏、Canvas、自绘 UI 可能读出来是空的**,这类场景回到 `screenshot` + `zoom`

#### `focus_window`
把指定窗口激活到前台(最小化会先还原),之后可以直接 `type_text` / `send_text`,或用返回的 `rect` 算坐标去点击。

| 参数 | 类型 | 说明 |
|---|---|---|
| hwnd | number | 窗口句柄(来自 `list_windows`) |
| title | string | 标题子串,不区分大小写,匹配 name / win32_title / class,取第一个命中 |

- `hwnd` 与 `title` 二选一,都不给会报错
- 返回: `{ok, hwnd, name, class, rect}`;失败时 `{ok:false, error}`,error 里带当前候选窗口列表
- 坑: ①目标窗口若以管理员权限运行,普通权限进程可能无法置前(Windows 限制),此时返回值会附 `note` 提示"可用 rect 直接点";②**置前成功 ≠ 输入框已聚焦**,通常还要 `click` 一下输入框再输入

### 5.2 鼠标类(全部坐标类)

#### `click`
点击鼠标。省略 `x`/`y` = **在鼠标当前位置原地点击**(不会跳到屏幕角落)。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| x, y | number | — | 目标点;两个都不给=原地点击 |
| coord | `"image"\|"screen"` | `"image"` | 坐标口径 |
| button | `left\|right\|middle\|x1\|x2` | `left` | `x1`/`x2` 是鼠标侧键 |
| clicks | int | 1 | 2 = 双击(server 固定 80ms 间隔,保证被系统识别) |
| hold_ms | number | 0 | 按压时长(0~5000),长按/按住用 |
| points | number[][] | — | 坐标序列,`[[x,y],[x,y,hold_ms],...]`,与 `x/y` 互斥,每点都点一次 |
| gap_ms | number | 0 | 序列相邻两点的间歇(**与双击间隔无关**) |
| move_mode | `instant\|smooth` | `instant` | 给了 `x/y` 时如何定位 |
| duration_ms | number | — | `move_mode="smooth"` 时的移动耗时 |

- 返回: `{count, points(换算后的屏幕坐标), gap_ms, hold_ms, button, coord, img_size, screen_size}`
- 示例: `{"x":600,"y":527}` / 长按 `{"x":100,"y":200,"hold_ms":800}` / 双击 `{"x":10,"y":20,"clicks":2}` / 序列 `{"points":[[100,200],[300,400]],"gap_ms":300}`
- 坑: ①双击请用 `clicks=2`,不要连调两次 click;②序列中途异常不回滚,错误会说明已完成几个点;`hold_ms` 超 5000 直接报错

#### `move`
**只移动,不点击**(与 `click` 彻底解耦:要"移过去 → 截图确认 → 再点"就先 `move` 再 `click`)。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| x, y | number | — | 单点目标 |
| coord | `"image"\|"screen"` | `"image"` | 坐标口径 |
| mode | `smooth\|instant` | `smooth` | 平滑(插值+抖动+加速)或瞬移 |
| duration_ms | number | — | 整段移动耗时(仅 smooth 生效) |
| points | number[][] | — | 序列,与 `x/y` 互斥;三元点覆盖本点耗时 |
| gap_ms | number | 0 | 序列相邻两点间歇 |

- 返回: `{count, points, gap_ms, duration_ms, coord, ...}`
- 坑: 既不传 `x/y` 也不传 `points` 会报错(移动必须给目标)

#### `drag` / `mouse_down` / `mouse_up`
- `drag`: `{x1,y1,x2,y2, coord?, button?, duration_ms?}`,按住左键从起点拖到终点后松开
- `mouse_down`: `{x,y, coord?, button?}` 按下并保持(之后可 `move` 多次再 `mouse_up`,做精确拖拽/选区)
- `mouse_up`: `{x,y, coord?, button?}` 松开
- 返回: `{ok:true, coord, img_size, screen_size}`

#### `scroll`
把鼠标移到 (x,y) 再滚轮。`{x?, y?, coord?, dx?, dy}`;`dy` 负=向下滚(一次约 -300),正=向上滚。省略 `x/y` 用屏幕中心。返回带口径块。

#### `slide`
**相对**滑动(不是绝对坐标):`{dx, dy, coord?}`,dx/dy 是位移量,默认按 `image` 口径缩放。用于游戏转视角等场景(带抖动与加减速)。

### 5.3 键盘类

#### `type_text`
向**当前聚焦**的输入位置输入文字,不移动鼠标也不点击。

- 参数: `{text}`
- 行为: 中文/长文本走剪贴板粘贴(**会临时占用系统剪贴板**);英文/短文本逐字符输入并带 40~120ms 随机节奏
- 坑: 输入前必须让目标输入框聚焦(`click` 或 `focus_window`);要"输入并发送"用 `send_text`

#### `send_text`(高层,推荐)
输入文字并**按一个组合键提交**,等价于 `type_text` + `combo`,但把"发送"这一步标准化,避免漏掉目标应用的发送快捷键。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| text | string | 必填 | 要输入的文字(中文自动走剪贴板) |
| submit | string \| string[] | — | 提交组合键,如 `"ctrl+enter"` 或 `["ctrl","enter"]` |
| clear_first | bool | false | 先 `Ctrl+A` 全选再删除,清掉输入框旧内容 |

- 返回: `{ok, text_len, submit_keys}`
- **重要坑**: 很多 GUI 里**单独 `enter` 只是换行**。实测 DeepSeek Harness 网页端的发送快捷键是 **`ctrl+enter`**;IM/聊天类常见 `enter`,编辑器多为 `ctrl+enter`。不确定时先 `clear_first` 试发一条短消息,并截图确认
- 本工具**不会**点击/聚焦:先用 `click` 点输入框或 `focus_window` 目标窗口

#### `press_key` / `key_down` / `key_up` / `combo`
- `press_key`: `{key}` 敲一次(enter/escape/tab/space/backspace/delete/f1-f12/up/down/left/right/home/end/ctrl/alt/shift…)
- `key_down` / `key_up`: 按住 / 释放(配合实现长按或按住修饰键再操作)
- `combo`: `{keys:["ctrl","enter"]}` 按顺序按下再逆序释放
- 坑: 单独 `press_key("enter")` 在网页聊天框通常只换行,发送用 `combo([ctrl,enter])` 或 `send_text(submit=...)`

### 5.4 其他工具

| 工具 | 参数 | 说明 |
|---|---|---|
| `wait` | `{seconds}` | 等待。坑:不要用长 wait 代替观察,等完最好再 `screenshot` |
| `set_brush` | `{size}` | 画笔大小 1~200,返回实际生效值 |
| `draw_curve` | `{points, coord?, inject?}` | 按控制点画中点贝塞尔曲线;`inject=true` 走触笔压力通道(对 Win32 绘图软件有效),false 走鼠标拖动 |
| `switch_mode` | `{mode:"game"\|"draw"\|"work"}` | 切换操控模式,返回 `{ok, mode}` |
| `set_config` | `{key, value}` | 改 `config.json` 白名单参数(见 §9.3),越权会被拒 |
| `check_vision` | 无 | 用 1×1 测试图检查配置的模型是否支持图像输入 |
| `run_actions` | `{actions:[...]}` | 批量按顺序执行,返回 `{ok, results:[...]}`。批内**不额外等待**(需要停顿请插 `wait`);`zoom` 返回图像,**不在批量里支持**,请单独调用 |

### 5.5 `run_actions` 动作别名表

每个元素是"动作对象",含 `action` 字段 + 该动作参数(与对应单工具一致,坐标类也可带 `coord`):

| action | 关键参数 | 说明 |
|---|---|---|
| `click` / `double_click` / `right_click` | `x,y`(或 `points`) | `double_click`/`right_click` 是 `click` 的便捷别名 |
| `move` | `x,y,mode,duration_ms` 或 `points,gap_ms` | 只移动 |
| `drag` | `x1,y1,x2,y2` | 拖拽 |
| `mouse_down` / `mouse_up` | `x,y` | 按放 |
| `scroll` | `x?,y?,dx?,dy` | 滚轮 |
| `slide` | `dx,dy` | 相对滑动 |
| `type_text` | `text` | 输入 |
| `send_text` | `text,submit?,clear_first?` | 输入并提交 |
| `press_key` / `key_down` / `key_up` | `key` | 按键 |
| `combo` / `hotkey` | `keys` | 组合键(两者等价) |
| `wait` | `seconds` | 等待 |
| `zoom_to_screen` | `px,py` | 放大图像素换算 |
| `focus_window` | `hwnd` 或 `title` | 窗口置前 |
| `switch_mode` | `mode` | 切模式 |
| `set_config` | `key,value` | 改白名单配置 |
| `set_brush` | `size` | 画笔 |
| `draw_curve` | `points,inject?` | 曲线 |
| `take_screenshot` | — | 占位:提示"截图请在批量后单独调用" |

---

## 6. 典型工作流

### 6.1 看准再点(推荐给所有"点小目标"的场景)

```jsonc
{"name":"screenshot","arguments":{}}                       // 粗看: 得到 1066x600 的图
{"name":"zoom","arguments":{"x":600,"y":527,"factor":6,"src":200}}   // 精看输入框附近
{"name":"zoom_to_screen","arguments":{"px":430,"py":300}}  // 放大图里的像素 -> img_x/img_y
{"name":"click","arguments":{"x":<img_x>,"y":<img_y>}}     // 用 image 口径点击
```

### 6.2 给某个窗口发一条消息(端到端实测通过)

```jsonc
{"name":"list_windows","arguments":{}}                       // 拿到 hwnd / 标题 / rect
{"name":"focus_window","arguments":{"title":"DeepSeek Harness"}}  // 置前(也能按 hwnd)
{"name":"screenshot","arguments":{}}                         // 建立 image 口径
{"name":"click","arguments":{"x":600,"y":527}}               // 点输入框(坐标来自这张图)
{"name":"send_text","arguments":{"text":"...","submit":"ctrl+enter"}}
{"name":"screenshot","arguments":{}}                         // 核对: 输入框是否已清空、消息是否上屏
```

> 实测提醒: 光按 `enter` 只会换行,必须 `ctrl+enter`;发送后**一定再截图确认**,不要假设成功。

### 6.3 屏幕上已有屏幕坐标时

```jsonc
{"name":"click","arguments":{"x":1080,"y":948,"coord":"screen"}}
```

### 6.4 批量操作 + 一次确认

```jsonc
{"name":"run_actions","arguments":{"actions":[
  {"action":"move","x":400,"y":300,"mode":"smooth","duration_ms":400},
  {"action":"click","x":400,"y":300},
  {"action":"wait","seconds":0.5},
  {"action":"type_text","text":"hello"},
  {"action":"combo","keys":["ctrl","enter"]}
]}}
```

---

## 7. Python 内部 API(改代码时看这一节)

分层:**原语层** `input_engine/` → **服务层** `computer_core/` → **MCP 层** `computer_mcp/` → **AI 层** `ai_mode/`。
MCP 工具只是服务层方法的薄包装;除了 `screenshot` / `zoom` / `list_windows` 这类要返回图像或大段文本的,其余都走 `_run_single`。

### 7.1 `input_engine/mouse.py`(原语)

| 名称 | 签名 / 值 | 说明 |
|---|---|---|
| `BUTTONS` | `{"left","right","middle","x1","x2"} → pynput.Button` | 未知键抛 `ValueError` |
| `HOLD_MS_MAX` | `5000` | 单次按压上限 |
| `DOUBLE_CLICK_INTERVAL_MS` | `80` | 同一次点击内部多次 down/up 的间隔 |
| `GAP_MS_DEFAULT` | `0` | 序列相邻两点间歇默认值 |
| `normalize_points(points, default_t)` | → `[(x, y, t), ...]` | 纯函数,序列校验/解包 |
| `move(x=None, y=None, mode="smooth", duration_ms=None, points=None, gap_ms=0, cfg=None)` | → None | 只移动 |
| `click(button="left", x=None, y=None, hold_ms=0, clicks=1, interval_ms=None, at=None, points=None, gap_ms=0, cfg=None)` | → None | 只点击;`x/y` 保留在**原有位置参数**上以兼容旧调用 |
| `move_to / move_absolute / move_relative / position` | | 既有插值移动原语 |
| `midpoint_bezier_curve / quadratic_bezier / replay_path` | | 曲线路径 |
| `drag / double_click / right_click / _btn` | | 既有便捷函数 |

### 7.2 其余原语

- `input_engine/keyboard.py`: `tap(key)` / `press(key)` / `release(key)` / `combo(keys)`
- `input_engine/text.py`: `type_text(text, cfg)` —— 中文或长文本走剪贴板,其余逐字符
- `input_engine/pen.py`: `PenInjector`(WM_POINTER 注入)、`PressureMapper`(速度→压力)

### 7.3 `computer_core/service.py`(`ComputerService`)

| 方法 | 签名 | 返回 |
|---|---|---|
| 屏幕 | `screen_size()` | `(w, h)` |
| 截图 | `screenshot(scale?, quality?)` / `screenshot_png()` / `screenshot_model(max_pixels=640000)` / `screenshot_base64()` | bytes / `(bytes, w, h)` |
| 移动 | `move(x=None, y=None, mode="smooth", duration_ms=None, points=None, gap_ms=0)` | 执行记录 dict |
| 点击 | `click(x=None, y=None, button="left", clicks=1, hold_ms=0, interval_ms=None, move_mode="instant", points=None, gap_ms=0, duration_ms=None)` | 执行记录 dict |
| 拖拽/滚轮 | `drag(x1,y1,x2,y2,duration_ms=400)` / `scroll(x,y,dx=0,dy=-300)` | None |
| 按放 | `mouse_down(x,y,button="left")` / `mouse_up(x,y,button="left")` | None |
| 键盘 | `press_key/key_down/key_up/hotkey/combo` / `type_text(text)` | None |
| 输入并提交 | `send_text(text, submit_keys=None, clear_first=False)` | `{ok, text_len, submit_keys}` |
| 窗口 | `describe_windows(max_items=30)` / `focus_window(hwnd=None, title=None)` | str / dict |
| 放大镜 | `zoom_tool(x=None, y=None, factor=10, src=100)` / `zoom_to_screen(px, py)` | `(bytes, meta)` / dict |
| 绘画 | `set_brush(n)` / `draw_curve(points, inject=True)` | int / dict |
| 模式/状态 | `switch_mode(mode)` / `get_state()` | dict |
| 配置 | `set_config(dotted, value)` / `get_config(dotted=None)` / `reload_config()` | dict |

### 7.4 `computer_core/observe.py`(放大镜 + 反算)

- `zoom(x=None, y=None, factor=10, src=100, quality=85, max_out=1200) -> (jpeg_bytes, meta)`
  meta 含 `screen_rect / factor / src_size / out_size / cursor / seq / to_screen / from_screen`;**成功即把本次映射记为"最近一次观察"**
- `clamp_region(cx, cy, src, screen_size) -> (l,t,r,b)`、`effective_factor(src, factor, max_out) -> int`(纯函数,有单测)
- `px_to_screen(px, py, meta=None) -> (sx, sy)`:放大图像素 → 屏幕坐标;超界 `ValueError`,无记录 `RuntimeError`
- `screen_to_px(sx, sy, meta) -> (px, py)`、`last_meta() -> dict | None`
- 状态: `_LAST`(最近一次 `{meta, img}`)、`_seq_counter`、`_LOCK`(threading.Lock)
- 插值固定 `Image.Resampling.NEAREST`(老 Pillow 回退 `Image.NEAREST`)——**不要改成 LANCZOS/BILINEAR**,会把 1px 细线糊掉

### 7.5 `computer_core/uia.py`(UIA 观察,纯 ctypes)

- 常量: `CLSID_CUIAutomation` / `IID_IUIAutomation` / `CLSCTX_INPROC_SERVER=1` / `SW_RESTORE=9` / `CONTROL_TYPES`(50000~50040 常用子集)
- 实测锁定的 vtable 索引(改动前必须重新实测):
  `IUIAutomation`: 5=GetRootElement, 6=ElementFromHandle
  `IUIAutomationElement`: 21=ControlType, 23=Name, 29=AutomationId, 30=ClassName, 43=BoundingRectangle
- `UIA` 类: `element_from_hwnd(hwnd)` / `describe(elem)` / `list_windows(visible_only=True, with_title_only=True)` / `format_windows(max_items=30)` / `find_window(hwnd=None, title=None)` / `focus(hwnd) -> bool`
- 约束: COM STA —— `UIA` 实例只能在创建它的线程用;读 BSTR 与元素引用**目前不释放**(已知泄漏,量级可忽略)

### 7.6 `ai_mode/controller.py`(AI 指挥官)

- `SYSTEM_PROMPT`:动作表是**兼容性契约**,只允许追加、不允许删改已有行
- `AIController._execute(action)`:与 `_run_single` 同构,但坐标是**屏幕坐标**(AI 模式截图与屏幕 1:1)
- AI 模式的 `zoom` 只返回元数据(动作级图像未接入对话)

---

## 8. 扩展指南:新增一个 MCP 工具 / 动作

按"先判断层次 → 原语 → 服务 → MCP → AI → 测试 → 文档 → 提交"的顺序做:

**Step 0 判断放哪层**
- 只是鼠标/键盘的新玩法 → 原语层 + 服务层
- 新的"观察"能力 → `observe.py`(图像类)或 `uia.py`(窗口/控件类)
- 只是把已有能力组合起来 → 优先做成**高层工具**(如 `send_text` = type_text + combo),不要新增原语

**Step 1 写失败测试**(项目没有 pytest,统一 `unittest`)
```python
# tests/test_xxx.py
import sys, os, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))
```
- **纯函数**(坐标换算、参数校验、映射表)写真实断言
- **会真的动鼠标/键盘**的部分用 `mock` 打桩(MCP 层打 `server.SERVICE.<method>`,原语层打 `mouse.mc`)
- 跑:`python -m unittest tests.test_xxx -v` → 确认**失败**

**Step 2 原语/服务层实现**
- 新参数一律**关键字**、带默认值;越界抛 `ValueError` 而不是静默夹取
- 服务层返回"执行记录"dict,便于调用方核对(参考 `click` 的 `{count, points, ...}`)
- 若方法本身无返回值,注意 MCP 层 `_coord_echo` 会把 `None` 兜成 `{"ok": true}`

**Step 3 MCP 层暴露**
1. `computer_mcp/server.py` 的 `TOOLS` 追加条目:**描述写全**「用途 / 坐标 / 参数 / 返回 / 坑」四要素,参数加 `description`
2. 若返回文本/图像 → 在 `handle_tool_call` 里特判(参考 `zoom`、`list_windows`)
3. 否则 → 在 `_run_single` 里加分支;坐标类动作记得用 `_cx/_cy/_cpoints` 换算并 `_coord_echo` 回显
4. 若该动作也应支持批量 → 更新 `run_actions` 的描述与 §5.5 别名表

**Step 4 AI 层同步**(可选但推荐)
- `ai_mode/controller.py` 的 `SYSTEM_PROMPT` **追加**一行动作说明(不删任何已有行)
- `AIController._execute` 加对应分支

**Step 5 测试与真机验证**
- `python -m unittest discover tests -v` 全绿(现在 78 个)
- MCP 冒烟: `.tmp/smoke_mcp.py`(工具数、schema、每个工具跑一次只读调用)
- 会动鼠标的能力**必须真机跑一次**,并把结果写进文档(本项目就是这么发现"Enter 只换行""focus 已在前台返回 false"的)

**Step 6 文档**
- 本文件:§4 总表、§5 详解、§5.5 别名表(如相关)、§11 变更记录
- `README.md`:模型可用动作表 / MCP 工具表 / 项目结构

**Step 7 提交**
- 中文 commit,一次提交只含一个逻辑改动,例如 `新增:xxx 工具(参数/返回/坑)`
- 不要提交 `vendor/`、`.tmp/`、`work/`、`__pycache__/`、试画 PNG(已在 `.gitignore`)

---

## 9. 测试 / 提交 / 配置

### 9.1 跑测试

```bash
cd H:\cu-a
python -m unittest discover tests -v      # 全量(当前 78 个)
python -m unittest tests.test_mouse_input -v
```

### 9.2 测试文件与覆盖

| 文件 | 覆盖 |
|---|---|
| `tests/test_mouse_input.py`(18) | 按键映射、移动/点击独立、序列校验与执行节奏、旧位置参数兼容 |
| `tests/test_service_click.py`(11) | 服务层参数转发、序列、校验、smooth 定位耗时 |
| `tests/test_observe_zoom.py`(13) | 区域钳制、倍率预算、放大图像素反算与往返一致 |
| `tests/test_uia_helpers.py`(7) | CLSID/IID、vtable 索引锁定、窗口匹配、focus 语义 |
| `tests/test_coord_and_tools.py`(18) | 坐标口径两种模式与回显、新工具分派、void 动作返回 |
| `tests/test_service_tools.py`(8) | `focus_window` / `send_text` 服务层行为 |
| `tests/test_mcp_dispatch.py`(3) | MCP 分派:省略坐标=原地点击 |

### 9.3 提交规范

- 中文 commit;一次提交只含**一个逻辑改动**(修 bug 与加功能分开)
- 提交前 `git status` 应是干净的(测试产物已在 `.gitignore`)
- 禁止: `git push` / `git remote` / `git reset --hard` / `git clean` / `git rebase` / `git merge`(除非用户明确要求)

### 9.4 `set_config` 白名单(`config.json` → `ai.whitelist`)

`mouse.jitter_px`、`mouse.move_steps`、`mouse.move_interval_ms`、`game.tap_interval_ms`、`game.slide.jitter_px`、`game.slide.smooth_ms`、`draw.brush.step`、`draw.replay_interval_ms`、`work.typing.delay_min_ms`、`work.typing.delay_max_ms`

白名单外的键一律拒绝;要开放新键,改 `config.json` 的 `ai.whitelist`。

---

## 10. FAQ / 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| 点击位置整体偏移(比例约 1.8 或 0.55) | 坐标口径混用:`image` 的值当成了屏幕坐标(或反之) | 先 `screenshot`,或显式传 `coord:"screen"`;用返回的 `points` 核对 |
| 文字没输进去 | 目标输入框没聚焦;或剪贴板被别的程序占用 | 先 `click` 输入框 / `focus_window`;必要时 `clear_first` 后重试 |
| 按了 Enter 但消息没发出去 | 该应用里 Enter 只换行 | 用 `send_text(submit="ctrl+enter")` 或 `combo(["ctrl","enter"])` |
| `list_windows` 列出的窗口很少 / 没有控件 | UIA 对该程序无效(游戏、Canvas、自绘 UI、部分 Electron) | 回到 `screenshot` + `zoom` |
| `focus_window` 返回 `ok:false` | 目标已在后台被系统前台锁定, 或它以管理员权限运行 | 返回值里带 `note` 与 `rect`:直接用 `rect` 算中心点 `click`;或让两端权限一致 |
| `zoom_to_screen` 报 `RuntimeError: 还没有 zoom 记录` | 进程内没成功 `zoom` 过(或进程重启了) | 先调用 `zoom` |
| `zoom_to_screen` 报 `ValueError: 像素超出放大图范围` | `px/py` 超出了那张放大图 | 按错误信息里的 `out_size` 校正;确认读的是最近一次 `zoom` 的图 |
| 序列点击中途报错 | 参数非法(通常是某个点) | 已完成点不回滚;按错误信息里的下标修参后重跑(注意鼠标已经动过) |
| `from input_engine import mouse` 失败 | 在受限沙箱里 `vendor/` 目录不可枚举 | 在沙箱外运行,或给沙箱 `danger-full-access` |
| `tools/list` 里工具数与文档不一致 | 版本不同 | 当前应为 **26** |

---

## 11. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-11 | **坐标契约**: 所有坐标工具支持 `coord=image\|screen` 并回显 `coord/img_size/screen_size`;`get_state`/`screenshot` 返回值带口径块 |
| 2026-09-11 | **新增高层工具** `focus_window`(窗口置前)与 `send_text`(输入并提交,解决"Enter 只换行")` — 工具数 24 → 26 |
| 2026-09-11 | 工具描述重写为「用途/坐标/参数/返回/坑」四要素;`focus` 修正在"窗口已在前台"时的误报失败;修复 void 动作返回 `null` 的回归 |
| 2026-09-11 | 新增 `move` / `zoom` / `zoom_to_screen` / `list_windows`;`click` 支持 `hold_ms`(长按)、`x1/x2`(侧键)、`points+gap_ms`(坐标序列);`move` 支持 `duration_ms` 与序列 — 工具数 20 → 24 |
| 2026-09-11 | 移动与点击彻底解耦;放大镜反算 `px_to_screen`;UIA 窗口级观察(纯 ctypes) |
| 更早 | 三模式插件(游戏/绘画/工作)+ AI 指挥官;initial MCP 20 工具 |
