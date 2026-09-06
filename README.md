# 电脑操控插件 (Computer Control Plugin)

一个 Windows 电脑操控插件, 提供三种模式 + AI 指挥官, 全局热键一键切换, 并注册为 DSH MCP 工具。

## 功能

| 模式 | 热键 | 功能 |
|---|---|---|
| 🎮 游戏模式 | `Ctrl+Shift+1` | 按键宏(连点/按住/组合) + 鼠标直线滑动(带随机抖动/弯曲防检测) |
| 🎨 绘画模式 | `Ctrl+Shift+2` | 曲线绘制(控制点系统) + `Alt+滚轮`调画笔大小 + 触笔压力注入 + 演示画布 |
| 💼 工作模式 | `Ctrl+Shift+3` | 文字输入(自然节奏) + 简单点击 + 简化快捷键 |
| 🤖 AI 模式 | 托盘→"AI 任务" | 截图→模型决策→自动切换三模式执行任务 |
| 退出 | `Ctrl+Shift+0` | 退出插件 |

## 🤖 AI 模式 (AI 指挥官)

托盘菜单 → "🤖 AI 任务" → 输入任务描述(如"打开计算器算 3+5") → AI 自动:
截图观察屏幕 → 模型决策动作 → 切换游戏/绘画/工作模式执行 → 直到完成。

- **模型配置**: `config.json` 的 `ai` 段。默认与 DSH 对话相同
  (base_url=`https://api.deepseek.com`, model=`deepseek-v4-flash-vision-exp`,
  api_key 自动读取 DSH 凭据/环境变量), 可手动改为其他 OpenAI 兼容 API
  (官方/本地 Ollama 均可)
- **识图能力检测**: 启动任务前发 1x1 测试图, 模型不支持图像输入时**报错禁用**
- **模型自改**: AI 可通过 `set_config` 修改白名单内的参数(见 `ai.whitelist`), 立即写入 config.json
- **坐标换算**: 截图按 `screenshot_scale` 缩放, AI 返回的坐标自动除以缩放比

## 🔌 DSH MCP 插件

本项目同时注册为 DSH 的 MCP 工具 (`mcp-computer`, 已写入 `H:\dsh-home\profiles\web\cordis.patch.yml`)。
**重启 DSH web 后**, agent 可直接调用:

- `mcp__computer__screenshot` - 截图观察屏幕
- `mcp__computer__click` / `drag` / `slide` - 鼠标操作
- `mcp__computer__type_text` / `press_key` / `combo` - 键盘输入
- `mcp__computer__switch_mode` - 切换三模式
- `mcp__computer__draw_curve` / `set_brush` - 绘画
- `mcp__computer__set_config` - 白名单改配置
- `mcp__computer__check_vision` - 检测模型识图能力
- `mcp__computer__run_ai_task` - 执行完整 AI 任务循环

## 安装

```bash
cd H:\cu-a
python -m pip install --target vendor pynput keyboard pystray Pillow
```

(依赖安装在项目 vendor 目录, 不占用 C 盘; 全部为纯 wheel 包, 无需编译)
> 若国内源不可用, 直接使用官方源: `python -m pip install --target vendor ...`

## 使用

```bash
cd H:\cu-a
python main.py
```

启动后系统托盘出现图标, 全局热键即可切换模式。

### 🎮 游戏模式
- 在 `config.json` 的 `game.macros` 中配置宏, 例如:
  ```json
  "macros": {
      "连点攻击": {"hotkey": "f6", "type": "tap_repeat", "key": "space", "times": 3},
      "视角左拉": {"hotkey": "f7", "type": "slide", "dx": -250, "dy": 0}
  }
  ```
- `slide` 宏使用高斯抖动 + 轻微弓形弯曲, 移动轨迹自然

### 🎨 绘画模式
- 托盘菜单 → "打开绘画画布" 打开演示画布
- 画布操作:
  - 手绘曲线, 松开自动 RDP 简化为控制点
  - 拖动控制点编辑曲线 / 点击空白插入 / 双击删除
  - `空格` 重放曲线(注入外部软件)
  - `Esc` 清空
- `Alt+滚轮` 调整画笔大小(悬浮指示跟随鼠标显示)
- `P` 键切换 鼠标输入 / 笔输入(压力) 通道
- 压力模拟: 移动慢 → 重压(线条粗), 移动快 → 轻扫(线条细), 范围 0-1024
- 笔输入通道通过 `WM_POINTER` 消息注入, 对 Win32 绘图软件有效; 部分软件
  需自带"鼠标模拟压力"选项配合以获得压感

### 💼 工作模式
- 快捷键列表见 `config.json` 的 `work.shortcuts` (copy/paste/undo 等)
- 中文输入自动走剪贴板, 英文逐字符自然节奏

## 配置

所有参数在 `config.json` (首次运行自动生成), 修改后重启生效。
参数注释见 `utils/config.py` 的 `DEFAULT_CONFIG`。

## 项目结构

```
main.py            入口: 托盘 + 全局热键 + 模式分发 + AI任务
computer_core/     操控服务(截图/动作/白名单配置)
ai_mode/           AI 指挥官(识图检测 + 任务循环)
computer_mcp/      MCP server(DSH 插件)
utils/             配置 / 日志 / 几何算法(RDP)
input_engine/      鼠标 / 键盘 / 文字 / 触笔压力注入
modes/             游戏 / 绘画 / 工作 模式
ui/                托盘 / 悬浮指示器 / 演示画布 / AI任务窗口
```

## 免责声明

游戏模式的防检测抖动仅用于合规自动化场景(解放双手/辅助操作), 请遵守目标
软件的服务条款与所在地区法律法规。
