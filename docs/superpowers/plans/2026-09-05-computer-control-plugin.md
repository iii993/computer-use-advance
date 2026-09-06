# 电脑操控插件(三模式)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> 日期: 2026-09-05 | 工作区: H:\\cu-a | 技术栈: Python 3.14

## 目标

构建一个 Windows 电脑操控插件, 提供三种模式(全局热键切换):

1. **游戏模式**: 按键宏 + 鼠标直线滑动(带高斯随机抖动防检测)
2. **绘画模式**: 曲线绘制(控制点系统) + 快捷调画笔大小 + WM_POINTER 触笔压力注入 + 演示画布
3. **工作模式**: 文字输入(自然节奏) + 简单点击 + 快捷键简化

## 文件结构

| 文件 | 职责 |
|---|---|
| `main.py` | 入口: 托盘 + 全局热键 + 模式分发 + 悬浮指示器 |
| `config.json` | 全部可配置参数 |
| `utils/logger.py` | 日志 |
| `utils/config.py` | 配置加载/保存 |
| `input_engine/mouse.py` | 鼠标移动(直线插值/抖动/贝塞尔曲线) |
| `input_engine/keyboard.py` | 键盘按键/组合/连点宏 |
| `input_engine/text.py` | 文字输入(英文直输+中文剪贴板) |
| `input_engine/pen.py` | WM_POINTER 压力注入 |
| `modes/game.py` | 游戏模式逻辑 |
| `modes/draw.py` | 绘画模式逻辑(曲线+控制点) |
| `modes/work.py` | 工作模式逻辑 |
| `ui/tray.py` | 托盘图标 |
| `ui/overlay.py` | 悬浮指示器(tkinter) |
| `ui/canvas.py` | 演示画布(tkinter, 控制点系统) |

## 任务拆分

### T1 脚手架+配置+日志
- 创建目录结构、`config.json`(三模式参数齐全)、`utils/config.py`(加载/保存/默认合并)、`utils/logger.py`
- 验证: `python -c "from utils.config import load_config; print(load_config())"`

### T2 核心输入引擎
- `input_engine/mouse.py`: `move_to`(直线插值+可抖动+加速减速), `click`, `drag`, `smooth_path`(中点贝塞尔)
- `input_engine/keyboard.py`: `press`, `tap`, `combo`, `tap_repeat`(连点)
- `input_engine/text.py`: `type_text`(自然延迟), 中文走剪贴板
- `input_engine/pen.py`: WM_POINTER 压力注入(ctypes)
- 验证: 各模块导入成功 + 鼠标移动 100px 无异常

### T3 游戏模式
- 按键宏注册: 连点/按住/组合
- 直线滑动: `move_to` 带高斯抖动, 非完美直线
- 验证: 手动运行, 鼠标滑动带自然抖动

### T4 绘画模式
- 曲线: 采样(2px) + 中点贝塞尔 + RDP简化控制点 + 拖动编辑 + 空格重放
- 画笔大小: Alt+滚轮 + 悬浮指示
- 压力: 速度→压力映射 + WM_POINTER 注入 + P键切换
- 演示画布: tkinter Canvas 压力笔迹
- 验证: 画布绘制曲线平滑, 控制点可拖动

### T5 工作模式
- 文字输入: 英文直输(随机延迟+按词停顿), 中文剪贴板
- 点击: 单击/双击/右键/拖拽
- 快捷键: 预置组合一键触发
- 验证: 记事本输入中英文正常

### T6 托盘+热键+切换+悬浮指示
- pystray 托盘: 启动/退出/模式切换菜单
- keyboard 全局热键: Ctrl+Shift+1/2/3 切换, Ctrl+Shift+0 退出
- overlay 悬浮指示: 跟随鼠标显示画笔大小
- 验证: 热键切换三模式, 托盘正常

### T7 集成测试+文档+提交
- README.md: 安装/使用/配置说明
- 全流程自测, git 提交(中文commit)

## 依赖
pynput keyboard pyautogui pystray Pillow (tkinter 自带)
