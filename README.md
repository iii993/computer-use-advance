# 电脑操控插件 (Computer Control Plugin) - 使用说明

一个 Windows 电脑操控插件: 提供 **游戏 / 绘画 / 工作** 三种操控模式 + **AI 指挥官** 自动模式,
支持全局热键切换、触笔压力模拟、DSH MCP 工具集成。

---

## 1 功能总览

| 模式 | 热键 | 功能 |
| --- | --- | --- |
| 🎮 游戏模式 | `Ctrl+Shift+1` | 按键宏(连点/按住/组合) + 鼠标直线滑动(带高斯抖动/弓形弯曲防检测) |
| 🎨 绘画模式 | `Ctrl+Shift+2` | 曲线绘制(控制点系统) + `Alt+滚轮`调画笔 + 触笔压力注入 + 演示画布 |
| 💼 工作模式 | `Ctrl+Shift+3` | 文字输入(自然节奏) + 简单点击 + 简化快捷键 |
| 🤖 AI 模式 | 托盘 → `"🤖 AI 任务"` | 截图→模型决策→自动切换三模式执行任务 |
| 退出 | `Ctrl+Shift+0` | 安全退出插件 |

> 所有热键与参数可在 `config.json` 中修改(首次运行自动生成)。

---

## 2 环境要求

- Windows 10/11
- Python 3.10+ (开发环境为 3.14)
- 依赖(自动安装到项目 `vendor` 目录, 不占 C 盘): `pynput` `keyboard` `pystray` `Pillow`

---

## 3 安装

### 方式一: 一键安装(推荐)

```bash
cd H:\cu-a
python -m pip install --target vendor pynput keyboard pystray Pillow
```

> 国内源不可用时直接用官方源: `python -m pip install --target vendor pynput keyboard pystray Pillow`
> 全部为纯 wheel 包, 无需编译。

### 方式二: 已打包

如果 `vendor/` 目录已存在依赖, 直接跳过安装步骤。

---

## 4 快速开始

```bash
cd H:\cu-a
python main.py
```

启动后:
1. 系统托盘出现蓝色图标
2. 全局热键 `Ctrl+Shift+1/2/3` 切换三模式
3. 托盘右键菜单可: 切换模式 / 打开绘画画布 / AI 任务 / 退出

---

## 5 🎮 游戏模式使用

### 按键宏

在 `config.json` 的 `game.macros` 中配置, 支持 4 种类型:

| type | 说明 | 参数 |
| --- | --- | --- |
| `tap_repeat` | 连点 | `key`(按键), `times`(次数) |
| `hold` | 按住一段时间 | `key`, `duration_ms`(毫秒) |
| `slide` | 相对滑动(带抖动) | `dx`, `dy`(像素) |
| `combo` | 组合键 | `keys`(["ctrl","c"]) |

示例:

```json
"macros": {
    "连点攻击": {"hotkey": "f6", "type": "tap_repeat", "key": "space", "times": 3},
    "视角左拉": {"hotkey": "f7", "type": "slide", "dx": -250, "dy": 0},
    "视角右拉": {"hotkey": "f8", "type": "slide", "dx": 250, "dy": 0},
    "复制粘贴": {"hotkey": "f9", "type": "combo", "keys": ["ctrl", "c"]}
}
```

### 直线滑动(防检测)

- 高斯抖动: 每个中间点叠加随机偏移(`jitter_px`, 默认 2.0px)
- 弓形弯曲: 轨迹呈轻微弧线(`curve_bend`, 默认 0.06)
- 加速-减速曲线: 起止慢、中间快, 模拟真人移动

---

## 6 🎨 绘画模式使用

### 打开演示画布

托盘菜单 → `"🖼 打开绘画画布"`

### 画布操作

| 操作 | 效果 |
| --- | --- |
| 手绘曲线 | 松开鼠标后自动 RDP 简化为控制点 |
| 拖动控制点 | 实时调整曲线形状 |
| 点击空白处 | 在最近线段插入新控制点 |
| 双击控制点 | 删除该控制点 |
| `空格` | 重放曲线(注入外部软件) |
| `Esc` | 清空画布 |

### 画笔大小

- `Alt+鼠标滚轮` 调整(上滚增大, 下滚减小, 默认每格 2px)
- 范围 1~200px, 悬浮指示器跟随鼠标显示当前大小, 3 秒无操作自动隐藏

### 触笔压力模拟

- `P` 键切换 鼠标输入 / 笔输入(压力) 通道
- 压力映射: **画得慢 → 重压**(线条粗), **画得快 → 轻扫**(线条细)
- 压力范围 0~1024, 经 4 点移动平均防抖
- 笔通道通过 `WM_POINTER` 消息注入目标窗口(对 Win32 绘图软件有效)
- 部分软件需开启自带"鼠标模拟压力"选项获得完整压感

### 曲线重放注入

画布按 `空格` 后: 曲线按真实速度+压力重放
- 笔通道: WM_POINTER 压力注入(带压感)
- 鼠标通道: 按住左键拖动

---

## 7 💼 工作模式使用

### 简化快捷键(预置)

| 名称 | 组合 |
| --- | --- |
| copy | `Ctrl+C` |
| paste | `Ctrl+V` |
| cut | `Ctrl+X` |
| undo | `Ctrl+Z` |
| select_all | `Ctrl+A` |
| switch_win | `Alt+Tab` |
| show_desktop | `Win+D` |
| lock | `Win+L` |

### 文字输入

- 英文/符号: 逐字符输入, 随机延迟(40~120ms) + 词间停顿, 模拟自然打字
- 中文/长文本(>20字符): 自动走剪贴板粘贴, 保持节奏

### 点击操作

- 单击 / 双击 / 右键 / 拖拽(均由 AI 或脚本调用)

---

## 8 🤖 AI 指挥官模式使用

### 开启方式

托盘菜单 → `"🤖 AI 任务"` → 输入任务描述 → 点`"▶ 开始执行"`

例如: `"打开计算器, 计算 3+5"` 或 `"在画布上画一条 S 形曲线"`

### 模型配置(默认与 DSH 对话相同)

```json
"ai": {
    "base_url": "https://api.deepseek.com",  // 默认与 DSH 对话相同(deepseek-official)
    "api_key": "",                            // 留空自动读 DSH 凭据/环境变量
    "model": "deepseek-v4-flash-vision-exp",  // 默认与 DSH 对话相同(支持视觉)
    "max_steps": 20,                            // 单任务最大步数
    "screenshot_scale": 1.0,                    // 1.0=原始分辨率, 坐标与屏幕一致
    "screenshot_quality": 60                    // JPEG 质量
}
```

密钥解析链: `config.json → 环境变量(DEEPSEEK_API_KEY/TOKENRHYTHM_API_KEY) → DSH 凭据文件(.credentials.yaml)`

### 对齐 codex computer-use 架构的改进

- **批量动作**: 模型一次返回 {"actions":[...]}, 全部执行后再截图反馈(减少API往返5-10倍)
- **滑动窗口上下文**: 截图只保留最近3轮, 历史动作用文本记录(防上下文爆炸)
- **细粒度动作**: wait/scroll/mouse_down/key_down/hotkey 等19种动作
- **解析容错**: 输出无法解析时把错误回传模型修正
- **坐标直用**: 截图原始分辨率, 模型坐标与真实屏幕一致, 免换算

### 识图能力检测(强制)

启动任务前自动发送 1x1 测试图:
- API 接受图像输入 → `✅ 通过`, 开始任务循环
- API 拒绝(400/404/422) → `❌ 报错禁用`, 提示换支持视觉的模型

### 执行流程

```
任务输入 → 识图检测 → [截图 → 模型决策(批量动作JSON) → 批量执行 → 截图反馈] 循环 → 完成/超步数
```

### 模型可用动作

| 动作 | 参数 |
| --- | --- |
| click / double_click / right_click | x, y |
| drag / mouse_down / mouse_up | 坐标/拖拽/按放 |
| scroll | x, y, dy (滚轮) |
| wait | seconds |
| key_down / key_up / hotkey | key / keys |
| type_text | text |
| press_key | key |
| combo | keys |
| slide | dx, dy |
| switch_mode | game / draw / work |
| set_config | key, value (仅白名单) |
| finish | result |

### 模型自改配置(白名单)

AI 可通过 `set_config` 修改 `ai.whitelist` 内的参数(立即生效):

```json
"whitelist": [
    "mouse.jitter_px", "mouse.move_steps", "mouse.move_interval_ms",
    "game.tap_interval_ms", "game.slide.jitter_px", "game.slide.smooth_ms",
    "draw.brush.step", "draw.replay_interval_ms",
    "work.typing.delay_min_ms", "work.typing.delay_max_ms"
]
```

白名单外的键一律拒绝, 防止模型破坏配置结构。

---

## 9 🔌 DSH MCP 插件集成

本项目已注册为 DSH 的 MCP 工具(名称 `mcp-computer`),
注册位置: `H:\dsh-home\profiles\web\cordis.patch.yml`

**重启 DSH web 后**, agent 可直接调用以下工具:

| 工具 | 用途 |
| --- | --- |
| `mcp__computer__screenshot` | 截取全屏(返回图像) |
| `mcp__computer__get_state` | 当前模式/光标/画笔 |
| `mcp__computer__click` / `drag` / `slide` | 鼠标操作 |
| `mcp__computer__type_text` / `press_key` / `combo` | 键盘输入 |
| `mcp__computer__switch_mode` | 切换 game/draw/work 模式 |
| `mcp__computer__draw_curve` / `set_brush` | 绘画操作 |
| `mcp__computer__set_config` | 白名单内改配置 |
| `mcp__computer__check_vision` | 检测模型识图能力 |
| `mcp__computer__run_ai_task` | 执行完整 AI 任务循环 |

---

## 🔧 配置文件全参数 (config.json)

| 路径 | 默认 | 说明 |
| --- | --- | --- |
| hotkeys.game / draw / work / quit | ctrl+shift+1/2/3/0 | 模式切换热键 |
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
| ai.base_url / model / api_key | 对话相同 | AI 模式模型配置 |
| ai.max_steps | 20 | AI 任务最大步数 |
| ai.screenshot_scale / quality | 1.0/85 | 截图(1.0=原始分辨率不缩放) / JPEG质量 |
| ai.whitelist | 10项 | 模型可修改的白名单 |

---

## ❓ 常见问题 (FAQ)

**Q: AI 任务提示"模型无识图能力"?**
A: 当前配置的模型不支持图像输入。把 `ai.model` 换成支持视觉的模型(如 `deepseek-v4-flash-vision-exp` / qwen-vl 系列), 或检查 `ai.base_url` 是否可达。

**Q: DSH agent 调用 `screenshot` 报错?**
A: DSH 对话模型不支持视觉时会收到 API 报错(正常现象)。在 DSH 设置中切换到支持视觉的模型即可。

**Q: 绘画压力注入外部软件没效果?**
A: WM_POINTER 注入对 Win32 传统绘图软件有效; 对 Chrome/UWP 等现代应用无效。可在目标软件中开启"鼠标模拟压力"选项, 或改用鼠标通道(`P` 键)。

**Q: 热键被占用/想改热键?**
A: 修改 `config.json` 的 `hotkeys` 段, 重启插件生效。

**Q: 如何让 AI 改配置?**
A: AI 通过 `set_config` 动作修改白名单内参数(见 `ai.whitelist`), 修改立即写入 config.json。

---

## 📂 项目结构

```
main.py            入口: 托盘 + 全局热键 + 模式分发 + AI任务
computer_core/     操控服务(截图/动作/白名单配置)
ai_mode/           AI 指挥官(识图检测 + 任务循环)
computer_mcp/      MCP server(DSH 插件)
utils/             配置 / 日志 / 几何算法(RDP)
input_engine/      鼠标 / 键盘 / 文字 / 触笔压力注入
modes/             游戏 / 绘画 / 工作 模式
ui/                托盘 / 悬浮指示器 / 演示画布 / AI任务窗口
config.json        全部配置(首次运行自动生成)
vendor/            本地依赖(不入库)
```

---

## ⚠ 免责声明

游戏模式的防检测抖动仅用于合规自动化场景(解放双手/辅助操作)。
请遵守目标软件的服务条款与所在地区法律法规。
