# computer-use-advance

把 **Windows 电脑操控**能力挂进 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的 **DSH 插件**。
挂上之后 agent 就能: 截图观察、移动/点击鼠标、敲键盘、枚举与激活窗口、切换输入法, 还能驱动 **Krita** 画画。

> ⚠️ 本仓库**只包含给 AI 用的 MCP 部分** —— 它是一个**没有界面**的后台服务。
> **不提供托盘图标和全局热键**: 那是"给人用"的另一种程序形态(`main.py` + `ui/`), 已从本仓库移除。

---

## 1 安装

### 从 GitHub 安装(推荐)

```powershell
# 1) 装包(dsh plugin 只是把参数转发给 profile 目录里的 pnpm)
dsh plugin --profile web add github:iii993/computer-use-advance

# 2) 启用: 把 "computer-use-advance" 加进 profile 的 package.json -> dsh.profile.bundles
#    路径: $DSH_HOME/profiles/web/package.json

# 3) 重启 DSH(改动 profile 配置也会触发 cordis HMR 热加载)
```

### 从本地目录安装

```powershell
dsh plugin --profile web add file:H:/cu-a
```

### 换机器 / 换路径

`cordis.patch.yml` 里的 python 与仓库路径是绝对路径。换机器跑一次即可自动改写:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### 这个包由什么组成

| 文件 | 作用 |
| --- | --- |
| `package.json` | `dsh.bundle.patch` 指向 patch —— `dsh plugin add` 靠这个字段识别它是插件 |
| `cordis.patch.yml` | 向 profile 插入 `mcp-computer` / `mcp-krita` 两个 mcp-client 条目 |
| `lib/index.js` | 空壳: 工具 schema 只在 `computer_mcp/server.py` 维护一份, 不在 JS 里重写 |
| `install.ps1` | 探测本机 python 与仓库路径并自动改写 patch |

---

## 2 工具清单(28 个)

挂载后 agent 会多出这些工具(前缀 `mcp__computer__*` / `mcp__krita__*`):

| 工具 | 用途 |
| --- | --- |
| `screenshot` | 截取全屏(返回图像) |
| `get_state` | 当前模式 / 光标位置 / 坐标口径 |
| `zoom` / `zoom_to_screen` | 10X NEAREST 放大镜观察 + 放大图像素换算回坐标 |
| `list_windows` | UIA 无障碍树窗口清单(支持 `title` 过滤与 `wait_seconds` 轮询) |
| `focus_window` | 按 hwnd / 标题激活窗口(**轮询确认前台真正切换完成**才报成功) |
| `click` / `drag` / `slide` | 鼠标操作; click 支持 `hold_ms` 长按、侧键 x1/x2、`points` 坐标序列 |
| `move` | 只移动不点击, 支持 `duration_ms` 与 `points`+`gap_ms` |
| `scroll` | 移动到指定位置后滚轮 |
| `press_key` / `key_down` / `key_up` / `combo` | 键盘按键与组合键 |
| `type_text` / `send_text` | 输入文字 / 输入并按组合键提交(如 ctrl+enter) |
| `get_input_method` / `switch_input_method` | **查/切输入法** —— 中文输入法会吞掉注入的按键 |
| `switch_mode` | 切换 game / draw / work 模式 |
| `draw_curve` / `draw_pressure_curve` / `set_brush` | 绘画(曲线 / 压感 / 画笔) |
| `liquify` / `smudge` | 液化 / 涂抹 |
| `set_config` | 修改白名单内的配置项 |
| `check_vision` | 检测当前模型是否支持图像输入 |
| `run_actions` | 批量按顺序执行多个动作 |

> 📘 完整接口文档(坐标契约 / 每个工具的参数与返回 / Python API / 新增工具步骤)见 [`docs/MCP接口文档.md`](docs/MCP接口文档.md)。

### 坐标口径(重要)

所有坐标类工具都支持 `coord=image|screen`:

- `image`(默认): 坐标是**最近一次 `screenshot` 返回图像内**的像素
- `screen`: 屏幕物理像素
- 返回值会回显 `coord` / `img_size` / `screen_size`, 可随时核对

---

## 3 环境要求

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11 |
| Python | 3.10+(开发环境 3.14) |
| 依赖 | `pynput`、`Pillow`(装到项目 `vendor/`, 不占 C 盘) |
| Krita 部分 | Krita 需在运行, 且已装 `krita_mcp` 桥接插件 |

```powershell
cd H:\cu-a
python -m pip install --target vendor pynput Pillow
```

---

## 4 配置(config.json)

| 路径 | 默认 | 说明 |
| --- | --- | --- |
| mouse.move_steps | 24 | 直线移动插值步数 |
| mouse.move_interval_ms | 5 | 每步间隔(ms) |
| mouse.jitter_px | 1.2 | 移动抖动幅度(px) |
| mouse.accel_curve | true | 加速减速曲线 |
| game.tap_interval_ms | 180 | 连点间隔(ms) |
| game.tap_jitter_ms | 30 | 连点间隔抖动(ms) |
| game.slide.smooth_ms | 800 | 滑动时长(ms) |
| game.slide.jitter_px | 2.0 | 滑动抖动(px) |
| game.slide.curve_bend | 0.06 | 弓形弯曲比例 |
| draw.sample_min_dist | 2 | 采样最小位移(px) |
| draw.rdp_epsilon | 4 | RDP 简化容差(px) |
| draw.bezier_segments | 8 | 贝塞尔细分点数 |
| draw.brush.step | 2 | 滚轮每格画笔增减 |
| draw.pen.pressure_min/max | 50/1024 | 压力范围 |
| draw.pen.slow_speed / fast_speed | 50/500 | 速度映射阈值(px/s) |
| work.typing.delay_min_ms / delay_max_ms | 40/120 | 打字速度范围 |
| work.typing.clipboard_threshold | 20 | 剪贴板输入阈值(字符) |
| ai.base_url / model / api_key | 对话相同 | AI 识图模型配置(`check_vision` 用) |
| ai.max_steps | 20 | AI 任务最大步数 |
| ai.screenshot_scale / quality | 1.0/85 | 截图缩放 / JPEG 质量 |
| ai.whitelist | 10 项 | `set_config` 可修改的白名单 |

> `hotkeys.*` 字段仍在 config.json 里, 但**已不生效** —— 全局热键属于已移除的桌面程序部分。

---

## 5 常见问题 (FAQ)

**Q: DSH agent 调用 `screenshot` 报错?**
A: DSH 对话模型不支持视觉时会收到 API 报错(正常现象)。在 DSH 设置里切换到支持视觉的模型即可。也可先调 `check_vision` 自检。

**Q: 调 `switch_input_method` 前要注意什么?**
A: 中文(微软拼音)输入法会拦截注入的按键 —— 按键不进目标程序, 甚至吞掉 KeyUp 造成"卡键"(角色一直往一个方向走)。**玩游戏 / 连发按键前先 `switch_input_method(layout="en")`**, 并用 `get_input_method` 复查 `is_english=true`。

**Q: `focus_window` 返回 ok 了, 按键还是打到别的窗口?**
A: `SetForegroundWindow` 是**异步**的。本项目已改为轮询确认前台真正切换完成才报成功, 并在返回值里给出 `foreground` / `focus` 供核对。若仍打偏, 检查是否有别的程序抢占前台。

**Q: 改了 Python 代码不生效?**
A: MCP 子进程是常驻的。重启 DSH, 或在 profile 配置里改一处触发 cordis HMR 重载。

**Q: 工具描述 / 中文参数乱码?**
A: Windows 下 Python 的 stdio 默认走 GBK。patch 里已设 `PYTHONIOENCODING=utf-8`。

**Q: 绘画压力注入外部软件没效果?**
A: WM_POINTER 注入对 Win32 传统绘图软件有效; 对 Chrome / UWP 等现代应用无效。可在目标软件中开启"鼠标模拟压力"选项。

**Q: 如何让 agent 修改配置?**
A: 通过 `set_config` 工具修改白名单内参数(见 `ai.whitelist`), 修改立即写入 config.json。

---

## 6 项目结构

```
computer_mcp/      MCP server(DSH 插件的入口, 28 个工具在这里注册)
computer_core/     操控服务(截图 / 动作 / 白名单配置) + observe.py(10X 放大镜 / 像素反算) + uia.py(UIA 窗口观察)
input_engine/      鼠标 / 键盘 / 文字 / 触笔压力注入 + ime.py(输入法控制)
modes/             游戏 / 工作 模式(game/work 被 MCP 直接调用)
ai_mode/           AI 指挥官(识图检测 + 任务循环, 被 MCP 调用)
krita_mcp/         Krita 绘画桥接(MCP server + Krita 插件)
utils/             配置 / 日志 / 几何算法(RDP)
docs/              接口文档
tests/             unittest 测试套件
config.json        全部配置(首次运行自动生成)
vendor/            本地依赖(不入库)
```

---

## ⚠ 免责声明

游戏模式的防检测抖动仅用于合规自动化场景(解放双手 / 辅助操作)。
请遵守目标软件的服务条款与所在地区法律法规。
