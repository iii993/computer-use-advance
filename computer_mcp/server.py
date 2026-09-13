"""MCP stdio server: 把"电脑操控"能力暴露为标准 MCP 工具。

坐标系契约(详见 docs/MCP接口文档.md):
  1. 所有坐标工具都接受可选参数 coord: "image"(默认) | "screen"
       - coord="image": 入参是"最近一次 screenshot 返回的图像内像素"; server 按该图像尺寸换算成屏幕坐标
       - coord="screen": 入参就是屏幕物理像素, 不做任何换算
  2. 每个坐标类工具的返回值都回显 coord / img_size / screen_size, 调用方据此自检口径
  3. 未截图时 img_size == screen_size(1:1 等价)
  4. 推荐工作流: screenshot(粗看) -> zoom(精看) -> zoom_to_screen(换算) -> click/move
     若手上已经是屏幕坐标, 显式传 coord="screen" 最稳

协议: MCP (JSON-RPC 2.0 over stdio, 每行一条消息)。stdout 只输出协议, 日志走 stderr/文件。
"""
import base64
import json
import os
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENDOR = os.path.join(_BASE, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from utils.logger import setup_logger
_logger = setup_logger("mcp")
for h in list(_logger.handlers):
    if hasattr(h, "stream") and getattr(h.stream, "name", "") == "<stdout>":
        _logger.removeHandler(h)

from computer_core.service import ComputerService
from ai_mode.controller import AIController

SERVICE = ComputerService()
AI = AIController(SERVICE)

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "computer", "version": "3.0.0"}

_COORD_HELP = "坐标口径: 默认 coord=\"image\"(最近一次截图内的像素); 也可传 coord=\"screen\"(屏幕物理像素)。"


# ---------- 坐标口径: image(截图内像素) / screen(屏幕物理像素) ----------
# _img_w/_img_h 由最近的 screenshot 更新; 未截图时等于屏幕尺寸(1:1).
_SCREEN_W, _SCREEN_H = SERVICE.screen_size()
_img_w, _img_h = _SCREEN_W, _SCREEN_H

_COORD_MODES = ("image", "screen")
# 需要坐标换算/回显口径的动作
_COORD_ACTS = {"click", "double_click", "right_click", "drag", "mouse_down", "mouse_up",
               "scroll", "slide", "move", "zoom", "zoom_to_screen", "draw_curve"}


def _set_img_size(w: int, h: int):
    global _img_w, _img_h
    _img_w, _img_h = max(1, w), max(1, h)


def _coord_of(action: dict) -> str:
    c = str(action.get("coord") or "image").lower()
    if c not in _COORD_MODES:
        raise ValueError(f"未知 coord: {c} (可选 image/screen)")
    return c


def _img_x(v) -> float:
    return float(v) * _SCREEN_W / _img_w


def _img_y(v) -> float:
    return float(v) * _SCREEN_H / _img_h


def _cx(v, coord: str = "image") -> float:
    """按口径把 x 换到屏幕坐标。"""
    return float(v) if coord == "screen" else _img_x(v)


def _cy(v, coord: str = "image") -> float:
    return float(v) if coord == "screen" else _img_y(v)


def _cpoint(p, coord: str = "image") -> tuple:
    return (_cx(p[0], coord), _cy(p[1], coord))


def _cpoints(points, coord: str = "image") -> list:
    """坐标序列 -> 屏幕坐标(保留三元点的第三个值, 如每点的耗时)。"""
    out = []
    for p in points or []:
        pt = [_cx(p[0], coord), _cy(p[1], coord)]
        if len(p) > 2:
            pt.append(p[2])
        out.append(pt)
    return out


def _img_cursor(cursor) -> list:
    return [round(cursor[0] / _SCREEN_W * _img_w, 1),
            round(cursor[1] / _SCREEN_H * _img_h, 1)]


def _to_img_xy(sx, sy) -> list:
    """屏幕坐标 -> 图像内像素(img_size 口径)。"""
    return [round(sx / _SCREEN_W * _img_w, 1),
            round(sy / _SCREEN_H * _img_h, 1)]


def _to_img_rect(rect) -> list:
    return [_to_img_xy(rect[0], rect[1])[0], _to_img_xy(rect[0], rect[1])[1],
            _to_img_xy(rect[2], rect[3])[0], _to_img_xy(rect[2], rect[3])[1]]


def _coord_block(coord: str = "image") -> dict:
    """回显给调用方的口径信息。"""
    return {"coord": coord,
            "img_size": [_img_w, _img_h],
            "screen_size": [_SCREEN_W, _SCREEN_H],
            "hint": "image=最近一次截图内的像素(默认且推荐); screen=屏幕物理像素; "
                    "未截图时 img_size == screen_size"}


def _coord_echo(result, coord: str = "image") -> dict:
    """给坐标类动作的返回值附上口径与换算结果, 便于调用方自检。

    服务层有些方法(drag/scroll/mouse_down/mouse_up/slide)返回 None, 这里统一补成 {"ok": true},
    否则 JSON 层会返回 null, 调用方会以为失败。
    """
    if not isinstance(result, dict):
        result = {"ok": True}
    result.setdefault("coord", coord)
    result.setdefault("img_size", [_img_w, _img_h])
    result.setdefault("screen_size", [_SCREEN_W, _SCREEN_H])
    return result


def _parse_keys(v):
    """把 "ctrl+enter" / ["ctrl","enter"] 统一成 ["ctrl", "enter"]。"""
    if not v:
        return None
    if isinstance(v, str):
        parts = [p.strip() for p in v.replace("+", " ").replace(",", " ").split()]
        return parts or None
    return [str(p) for p in v]


TOOLS = [
    {"name": "screenshot", "description":
     "截取整个屏幕并返回图像(等比缩到约 64 万像素, 实际尺寸见返回值)。这是观察屏幕的首选入口, 同时它定义了后续坐标的 image 口径: 返回值里的 img_size 就是这张图的尺寸, 之后用 click/move 时直接填图内像素即可(默认 coord=\"image\")。"
     "返回: 图像 + 文本(含 img_size / screen_size / coord)。"
     "坑: 图像是缩放过的, 不要拿它去推屏幕像素, 交给 server 换算; 若你手上是屏幕坐标, 显式传 coord=\"screen\"。",
     "inputSchema": {"type": "object", "properties": {}}},

    {"name": "get_state", "description":
     "读取当前状态: 模式(game/draw/work)、光标位置、画笔大小、以及当前坐标口径块 coord(含 img_size/screen_size)。"
     "用途: (1) 确认「现在坐标按哪种口径解释」; (2) 查看鼠标现在在哪。返回 cursor 是 image 口径的像素。",
     "inputSchema": {"type": "object", "properties": {}}},

    {"name": "click", "description":
     "点击鼠标。【坐标】x/y 用 coord 指定的口径(默认 image=最近一次截图内像素); 省略 x/y 则在鼠标当前位置原地点击(不会跳到屏幕角落)。"
     "【参数】button=left/right/middle/x1/x2(侧键); clicks=连击次数(2 即双击, 间隔由 server 固定 80ms 保证被系统识别); hold_ms=按压时长毫秒(0~5000, 长按/按住不放用); "
     "points=坐标序列 [[x,y], [x,y,hold_ms], ...] 与 x/y 互斥, 每个点都点一次; gap_ms=序列相邻两点的间歇毫秒(与双击间隔无关); move_mode=instant(默认, 瞬移定位)/smooth(平滑移动后再点); duration_ms 配合 smooth。"
     "【返回】{count, points(换算后的屏幕坐标), coord, img_size, screen_size}。【坑】不要用两次 click 模拟双击; 序列中途异常不会回滚已执行的点。",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "coord": {"type": "string", "enum": ["image", "screen"],
                                              "description": _COORD_HELP},
                                    "button": {"type": "string", "enum": ["left", "right", "middle", "x1", "x2"]},
                                    "clicks": {"type": "integer", "description": "连击次数, 2=双击"},
                                    "hold_ms": {"type": "number", "description": "按压时长(毫秒), 0~5000, 默认 0"},
                                    "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                                               "description": "坐标序列 [[x,y], [x,y,hold_ms], ...], 与 x/y 互斥"},
                                    "gap_ms": {"type": "number", "description": "序列相邻两点的间歇(毫秒), 默认 0"},
                                    "move_mode": {"type": "string", "enum": ["instant", "smooth"]},
                                    "duration_ms": {"type": "number", "description": "move_mode=smooth 时的移动耗时(毫秒)"}}}},

    {"name": "move", "description":
     "只移动鼠标, 不点击(与 click 彻底分开: 需要「移过去->确认->再点」时先 move 再 click)。"
     "【坐标】x/y 用 coord 口径(默认 image)。单点: x/y (+ duration_ms 设整段移动耗时, 仅 mode=smooth 生效); 序列: points=[[x,y],[x,y,duration_ms],...] 依次移动, gap_ms 设点间间歇。"
     "【参数】mode=smooth(插值+抖动+加速, 默认)/instant(瞬移)。【返回】{count, points(屏幕坐标), gap_ms, duration_ms, coord}。",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "coord": {"type": "string", "enum": ["image", "screen"], "description": _COORD_HELP},
                                    "mode": {"type": "string", "enum": ["smooth", "instant"]},
                                    "duration_ms": {"type": "number", "description": "整段移动耗时(毫秒), 仅 smooth 生效"},
                                    "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                                               "description": "[[x,y], [x,y,duration_ms], ...], 与 x/y 互斥"},
                                    "gap_ms": {"type": "number", "description": "序列相邻两点间歇(毫秒)"}}}},

    {"name": "drag", "description":
     "按住左键从 (x1,y1) 拖到 (x2,y2) 后松开, 用于拖拽文件/画框/滑杆。坐标默认 image 口径。"
     "【参数】x1,y1,x2,y2 必填; 可选 button=left/right/middle。需要更细的手动控制时改用 mouse_down + move + mouse_up。",
     "inputSchema": {"type": "object",
                     "properties": {"x1": {"type": "number"}, "y1": {"type": "number"},
                                    "x2": {"type": "number"}, "y2": {"type": "number"},
                                    "coord": {"type": "string", "enum": ["image", "screen"], "description": _COORD_HELP},
                                    "button": {"type": "string", "enum": ["left", "right", "middle"]}},
                     "required": ["x1", "y1", "x2", "y2"]}},

    {"name": "mouse_down", "description":
     "在 (x,y) 按下鼠标键并保持(配合 mouse_up 实现手工拖拽/选区的精细控制)。坐标默认 image 口径, 参数 button=left/right/middle。",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "coord": {"type": "string", "enum": ["image", "screen"], "description": _COORD_HELP},
                                    "button": {"type": "string", "enum": ["left", "right", "middle"]}},
                     "required": ["x", "y"]}},

    {"name": "mouse_up", "description":
     "松开鼠标键(结束上一次 mouse_down)。坐标参数仅用于记录/兼容, 默认 image 口径。",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "coord": {"type": "string", "enum": ["image", "screen"], "description": _COORD_HELP},
                                    "button": {"type": "string", "enum": ["left", "right", "middle"]}},
                     "required": ["x", "y"]}},

    {"name": "scroll", "description":
     "把鼠标移到 (x,y) 后滚轮滚动。【坐标】x/y 默认 image 口径(省略则用屏幕中心)。【参数】dy 滚轮量(负=向下滚, 正=向上滚, 一次约 -300); dx 水平滚动。"
     "【坑】滚动效果取决于目标窗口是否有滚动条; 滚动后内容变了请重新 screenshot。",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "coord": {"type": "string", "enum": ["image", "screen"], "description": _COORD_HELP},
                                    "dx": {"type": "number"}, "dy": {"type": "number"}},
                     "required": ["dy"]}},

    {"name": "slide", "description":
     "相对滑动鼠标(相对位移, 不是绝对坐标!): dx/dy 是位移量, 默认按 image 口径缩放, 用于游戏转视角等防检测场景(带抖动与加减速)。"
     "【坑】dx/dy 为相对值, 传 coord=\"screen\" 时按屏幕像素位移原样使用。",
     "inputSchema": {"type": "object",
                     "properties": {"dx": {"type": "number"}, "dy": {"type": "number"},
                                    "coord": {"type": "string", "enum": ["image", "screen"], "description": _COORD_HELP}},
                     "required": ["dx", "dy"]}},

    {"name": "type_text", "description":
     "向当前聚焦的输入位置输入文字(不移动鼠标、不点击)。中文与长文本自动走剪贴板粘贴(会临时占用系统剪贴板), 英文/短文本逐字符输入并带自然节奏。"
     "【坑】输入前请确保目标输入框已聚焦(用 click 或 focus_window); 需要「输入并发送」请用 send_text。",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"}}, "required": ["text"]}},

    {"name": "send_text", "description":
     "【高层工具】向当前聚焦的输入位置输入文字, 并可按一个组合键提交(如发送聊天消息)。等价于 type_text + combo, 但把「发送」这一步标准化, 避免漏掉目标应用的发送快捷键。"
     "【参数】text=要输入的文字(中文自动剪贴板); submit=提交组合键, 字符串 \"ctrl+enter\" 或数组 [\"ctrl\",\"enter\"](注意: 很多 GUI 里单独 enter 只换行, 例如 DeepSeek Harness 网页端需要 ctrl+enter); clear_first=true 时先 Ctrl+A 全选再删除, 清掉输入框旧内容。"
     "【返回】{ok, text_len, submit_keys}。【坑】本工具不会点击/聚焦, 用之前请先 click 输入框或 focus_window 目标窗口。",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"},
                                    "submit": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                                               "description": "提交组合键, 如 \"ctrl+enter\" 或 [\"enter\"]; 不传则只输入不提交"},
                                    "clear_first": {"type": "boolean", "description": "先清空输入框(默认 false)"}},
                     "required": ["text"]}},

    {"name": "get_input_method", "description":
     "【观察通道】查当前输入法(IME)。省略 hwnd 时查**当前前台窗口**所在线程的布局。"
     "返回 {hkl, hkl_hex, lang_id, name, is_chinese, is_english, klid, hwnd}。"
     "【为什么要看它】中文(微软拼音)输入法会拦截注入的按键 —— 按键不进目标程序, 甚至吞掉 KeyUp 造成"
     "\"卡键\"(角色一直往一个方向走)。玩游戏/连发按键前先用本工具确认, 中文就用 switch_input_method 切英文。",
     "inputSchema": {"type": "object",
                     "properties": {"hwnd": {"type": "number", "description": "目标窗口句柄(省略 = 当前前台窗口)"}}}},

    {"name": "switch_input_method", "description":
     "【高层工具】切换输入法(IME): layout=\"en\"(英文) / \"zh\"(中文简体)。"
     "【为什么需要】玩游戏 / 连发 / 连续按键前**必须先切英文** —— 中文输入法会拦截注入的字符键(按键不进游戏),"
     " 还可能吞 KeyUp 造成\"卡键\"(角色一直往一个方向走)。"
     "【用法】先 focus_window 目标窗口再 switch_input_method(layout=\"en\")(省略 hwnd = 作用于当前前台窗口);"
     " 也可定向 switch_input_method(layout=\"en\", hwnd=527378)。"
     "【返回】{ok, layout, target_hwnd, before, requested, method, hwnd}。"
     "【坑】切换经 PostMessage 异步生效, 切完建议用 get_input_method 复查 is_english=true 再开始按键。",
     "inputSchema": {"type": "object",
                     "properties": {"layout": {"type": "string", "enum": ["en", "zh"],
                                               "description": "目标输入法: en=英文(游戏/连发必备), zh=中文简体"},
                                    "hwnd": {"type": "number", "description": "目标窗口句柄(省略 = 当前前台窗口)"}},
                     "required": ["layout"]}},

    {"name": "press_key", "description":
     "敲一次按键(按下并释放): 如 enter/escape/tab/space/backspace/delete/f1-f12/up/down/left/right/home/end/ctrl/alt/shift。"
     "【坑】单独 enter 在多数网页聊天框里只是换行, 发送请用 ctrl+enter(combo 或 send_text 的 submit)。",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}}, "required": ["key"]}},

    {"name": "key_down", "description":
     "按住某个键不放(配合 key_up 实现长按/按住修饰键)。参数 key 同 press_key。",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}}, "required": ["key"]}},

    {"name": "key_up", "description":
     "释放某个键(结束 key_down)。参数 key 同 press_key。",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}}, "required": ["key"]}},

    {"name": "combo", "description":
     "按一次组合键, 如 [\"ctrl\",\"c\"] / [\"alt\",\"tab\"] / [\"ctrl\",\"enter\"]。按键按顺序按下再逆序释放。",
     "inputSchema": {"type": "object",
                     "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
                     "required": ["keys"]}},

    {"name": "wait", "description":
     "等待若干秒(页面加载/动画/网络请求)。【坑】不要用长 wait 代替观察: 等完最好再 screenshot 确认状态。",
     "inputSchema": {"type": "object",
                     "properties": {"seconds": {"type": "number"}}, "required": ["seconds"]}},

    {"name": "set_brush", "description":
     "设置绘画模式的画笔大小(1~200, 超出会被夹到范围内)。返回实际生效的画笔大小。",
     "inputSchema": {"type": "object",
                     "properties": {"size": {"type": "integer"}}, "required": ["size"]}},

    {"name": "draw_curve", "description":
     "按控制点绘制一条平滑曲线(中点贝塞尔), 可选注入触笔压力(对 Win32 绘图软件有效)。"
     "【坐标】points=[[x,y], ...] 默认 image 口径。【参数】inject=true 走触笔压力通道, false 走鼠标拖动通道。",
     "inputSchema": {"type": "object",
                     "properties": {"points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                                    "coord": {"type": "string", "enum": ["image", "screen"], "description": _COORD_HELP},
                                    "inject": {"type": "boolean"}},
                     "required": ["points"]}},

    {"name": "switch_mode", "description":
     "切换操控模式: game(游戏: 宏/防检测滑动) / draw(绘画: 曲线/压力) / work(工作: 打字/点击)。返回 {ok, mode}。",
     "inputSchema": {"type": "object",
                     "properties": {"mode": {"type": "string", "enum": ["game", "draw", "work"]}},
                     "required": ["mode"]}},

    {"name": "set_config", "description":
     "修改 config.json 里的白名单参数(如 mouse.jitter_px), 立即生效并落盘。非白名单键会被拒绝; 返回 {ok, key, value} 或 {ok:false, error}。",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}, "value": {}},
                     "required": ["key", "value"]}},

    {"name": "check_vision", "description":
     "自检: 用 1x1 测试图检查当前配置的 AI 模型是否支持图像输入(用于 AI 指挥官模式)。返回是否通过。",
     "inputSchema": {"type": "object", "properties": {}}},

    {"name": "list_windows", "description":
     "【观察通道】列出当前可见的顶层窗口(UIA 无障碍树), 返回文本清单: hwnd / 控件类型 / 屏幕矩形 rect / 类名 class / 标题 name。"
     "用途: 不截图也能知道有哪些窗口、它们在哪、句柄是多少(句柄可直接给 focus_window)。"
     "【参数】title=只列标题匹配的窗口(匹配规则同 focus_window, 只给最精确那一档: title=\"Krita\" 不会把 \"krita.e - Everything\" 也列进来);"
     " wait_seconds=窗口还没出现时轮询等待的秒数(默认 0 不等待, 上限 60)。"
     "【坑】UIA 只对支持无障碍接口的程序有效(Win32/WPF/WinForms/UWP/大体上 Electron); 游戏/Canvas/自绘 UI 可能读不到内容, 这类场景回到 screenshot + zoom。",
     "inputSchema": {"type": "object",
                     "properties": {"title": {"type": "string", "description": "只看标题匹配的窗口(可省略)"},
                                    "wait_seconds": {"type": "number", "description": "轮询等待窗口出现的秒数, 默认 0(不等待), 常用 3~10"}}}},

    {"name": "focus_window", "description":
     "【高层工具】把指定窗口激活到前台(最小化会先还原), 之后可以直接 type_text / send_text 输入到它, 或按 rect 计算坐标去点击。"
     "【定位】二选一: hwnd(来自 list_windows, 精确) 或 title(不区分大小写, 按 完全相等 > 词边界(如 \"文档 - Krita\") > 前缀 > 子串 打分选最优, 并优先有实际面积的窗口)。"
     "【等待】wait_seconds>0 时轮询等待窗口出现(默认 0 不等待, 上限 60): 刚 Start-Process 拉起程序时窗口往往要几秒才注册, 传 wait_seconds=10 一次调用即可, 不必自己反复 list_windows 重试; 返回值带 attempts/waited_ms 便于判断。"
     "【歧义】title 命中多个窗口时, 返回值会带 other_candidates 列表 —— 那种情况请改用 hwnd。"
     "【返回】{ok, hwnd, name, class, rect, attempts, waited_ms} 或 {ok:false, error, attempts, waited_ms}(error 里带当前候选窗口列表)。"
     "【坑】目标窗口若以管理员权限运行, 普通权限进程可能无法前置(Windows 限制); 聚焦成功不等于输入框已聚焦, 通常还要 click 一下输入框。",
     "inputSchema": {"type": "object",
                     "properties": {"hwnd": {"type": "number", "description": "窗口句柄, 来自 list_windows"},
                                    "title": {"type": "string", "description": "窗口标题(不区分大小写; 完全相等 > 词边界 > 前缀 > 子串)"},
                                    "wait_seconds": {"type": "number", "description": "轮询等待窗口出现的秒数, 默认 0(不等待), 常用 3~10"}}}},

    {"name": "zoom", "description":
     "【观察通道】放大镜: 把 (x,y) 周围一小块屏幕放大(默认 10X, NEAREST 像素复制, 不做插值模糊)后返回图像, 用于看清小字/小图标/细线。"
     "【坐标】x/y 默认 image 口径(省略则用鼠标当前位置)。【参数】factor=倍率, src=取样边长(屏幕像素, 默认 100)。"
     "【返回】图像 + zoom_meta: {seq, factor, out_size, screen_rect(屏幕矩形), img_rect(图像口径矩形), coord, img_size}。"
     "【关键】放大图的像素与屏幕像素不是一回事: 把放大图里的 (px,py) 交给 zoom_to_screen 换成坐标, 不要自己心算。倍率与视野成反比(10X + 1200 上限只覆盖约 120x120)。",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "coord": {"type": "string", "enum": ["image", "screen"], "description": _COORD_HELP},
                                    "factor": {"type": "integer", "description": "放大倍率, 默认 10"},
                                    "src": {"type": "integer", "description": "取样边长(屏幕像素), 默认 100"}}}},

    {"name": "zoom_to_screen", "description":
     "【观察通道】把「最近一次 zoom 图像」里的像素坐标 (px,py)(左上角为 0,0)换算成可用坐标, 返回 {screen_x, screen_y, img_x, img_y, img_size, seq, factor}。"
     "click/move 直接用返回的 img_x/img_y(它们就是 image 口径)即可命中该像素。"
     "【坑】本工具不重新截图: 画面/窗口位置变化后请重新 zoom; 越界(px/py 超出图像)或从未 zoom 过会明确报错; seq 可用来核对「这是哪次 zoom 的图」。",
     "inputSchema": {"type": "object",
                     "properties": {"px": {"type": "number", "description": "放大图内的 x 像素"},
                                    "py": {"type": "number", "description": "放大图内的 y 像素"}},
                     "required": ["px", "py"]}},

    {"name": "run_actions", "description":
     "【批量】一次调用按顺序执行多个动作, 减少往返与 token。actions 是动作对象数组, 每个对象含 action 字段 + 该动作的参数(与对应单工具一致; 坐标类动作也支持 coord)。"
     "支持: click / double_click / right_click / drag / mouse_down / mouse_up / scroll / slide / move / type_text / send_text / switch_input_method / get_input_method / press_key / key_down / key_up / combo / hotkey / wait / zoom_to_screen / focus_window / switch_mode / set_config / set_brush / draw_curve / take_screenshot。"
     "【返回】{ok, results:[...]} 每项是各动作的结果。【坑】批内按顺序执行且不额外等待(需要停顿请插入 wait); zoom 返回图像, 因此不在批量里支持, 请单独调用。",
     "inputSchema": {"type": "object",
                     "properties": {"actions": {"type": "array", "items": {"type": "object"}}},
                     "required": ["actions"]}},
]


def _text_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _image_result(b64: str, mime: str = "image/jpeg", note: str = "") -> dict:
    content = [{"type": "image", "data": b64, "mimeType": mime}]
    if note:
        content.append({"type": "text", "text": note})
    return {"content": content}


# ---------- 单动作执行(坐标口径换算的统一入口) ----------
def _run_single(action: dict) -> dict:
    svc = SERVICE
    act = action.get("action") or action.get("name")
    try:
        coord = _coord_of(action)
        if act == "click":
            kw = {"button": action.get("button", "left"),
                  "clicks": int(action.get("clicks", 1)),
                  "hold_ms": float(action.get("hold_ms", 0)),
                  "gap_ms": float(action.get("gap_ms", 0)),
                  "move_mode": action.get("move_mode", "instant"),
                  "duration_ms": action.get("duration_ms")}
            if action.get("points"):
                return _coord_echo(svc.click(points=_cpoints(action["points"], coord), **kw), coord)
            if action.get("x") is None or action.get("y") is None:
                return _coord_echo(svc.click(**kw), coord)      # 不给坐标 = 原地点击
            return _coord_echo(svc.click(_cx(action["x"], coord), _cy(action["y"], coord), **kw), coord)
        elif act == "move":
            mode = action.get("mode", "smooth")
            dur = action.get("duration_ms")
            if action.get("points"):
                return _coord_echo(svc.move(points=_cpoints(action["points"], coord),
                                            gap_ms=float(action.get("gap_ms", 0)),
                                            mode=mode, duration_ms=dur), coord)
            return _coord_echo(svc.move(_cx(action.get("x", 0), coord), _cy(action.get("y", 0), coord),
                                        mode=mode, duration_ms=dur), coord)
        elif act == "zoom_to_screen":
            r = svc.zoom_to_screen(action["px"], action["py"])
            r["img_x"], r["img_y"] = _to_img_xy(r["screen_x"], r["screen_y"])
            r.update(_coord_block("image"))
            r["note"] = "click/move 请用 img_x/img_y(image 口径); 画面变化后请重新 zoom"
            return r
        elif act == "double_click":
            return _coord_echo(svc.click(_cx(action["x"], coord), _cy(action["y"], coord), clicks=2), coord)
        elif act == "right_click":
            return _coord_echo(svc.click(_cx(action["x"], coord), _cy(action["y"], coord), button="right"), coord)
        elif act == "drag":
            return _coord_echo(svc.drag(_cx(action["x1"], coord), _cy(action["y1"], coord),
                                        _cx(action["x2"], coord), _cy(action["y2"], coord),
                                        action.get("duration_ms", 400)), coord)
        elif act == "mouse_down":
            return _coord_echo(svc.mouse_down(_cx(action["x"], coord), _cy(action["y"], coord),
                                              action.get("button", "left")), coord)
        elif act == "mouse_up":
            return _coord_echo(svc.mouse_up(_cx(action["x"], coord), _cy(action["y"], coord),
                                            action.get("button", "left")), coord)
        elif act == "scroll":
            return _coord_echo(svc.scroll(_cx(action.get("x", _img_w // 2), coord),
                                          _cy(action.get("y", _img_h // 2), coord),
                                          action.get("dx", 0), action.get("dy", -1)), coord)
        elif act == "slide":
            return _coord_echo(svc.slide(_cx(action.get("dx", 0), coord),
                                         _cy(action.get("dy", 0), coord)), coord)
        elif act == "draw_curve":
            pts = [_cpoint(p, coord) for p in action.get("points", [])]
            return _coord_echo(svc.draw_curve(pts, action.get("inject", True)), coord)
        elif act == "switch_input_method":
            return svc.switch_input_method(action.get("layout", "en"), action.get("hwnd"))
        elif act == "get_input_method":
            return svc.get_input_method(action.get("hwnd"))
        elif act == "focus_window":
            return svc.focus_window(hwnd=action.get("hwnd"), title=action.get("title"),
                                    wait_seconds=action.get("wait_seconds", 0))
        elif act == "send_text":
            keys = action.get("submit_keys") or action.get("submit")
            return svc.send_text(action.get("text", ""), submit_keys=_parse_keys(keys),
                                 clear_first=bool(action.get("clear_first", False)))
        elif act == "type_text":
            svc.type_text(action.get("text", ""))
        elif act == "press_key":
            svc.press_key(action.get("key", ""))
        elif act == "key_down":
            svc.key_down(action.get("key", ""))
        elif act == "key_up":
            svc.key_up(action.get("key", ""))
        elif act == "combo":
            svc.combo(action.get("keys", []))
        elif act == "hotkey":
            svc.hotkey(action.get("keys", []))
        elif act == "wait":
            svc.wait(action.get("seconds", 1))
        elif act == "switch_mode":
            return svc.switch_mode(action.get("mode", ""))
        elif act == "set_config":
            return svc.set_config(action.get("key", ""), action.get("value"))
        elif act == "set_brush":
            return {"ok": True, "brush": svc.set_brush(action.get("size", 1))}
        elif act == "take_screenshot":
            return {"ok": True, "note": "截图将在下轮由模型主动调用"}
        else:
            return {"ok": False, "error": "未知动作: " + str(act)}
        return {"ok": True}
    except KeyError as e:
        return {"ok": False, "error": "缺少参数: " + str(e)}
    except Exception as e:
        _logger.exception("动作执行失败 %s", act)
        return {"ok": False, "error": str(e)}


def handle_tool_call(name: str, args: dict) -> dict:
    svc = SERVICE
    try:
        if name == "screenshot":
            img_bytes, w, h = svc.screenshot_model()
            _set_img_size(w, h)                      # 这一步把 image 口径更新为这张图
            b64 = base64.b64encode(img_bytes).decode()
            note = json.dumps({
                "img_size": [w, h], "screen_size": [_SCREEN_W, _SCREEN_H], "coord": "image",
                "hint": f"后续坐标默认按 image 口径解释: 直接填这张图内的像素(0-{w}, 0-{h}); "
                        f"若你手上是屏幕坐标请传 coord=\"screen\"",
            }, ensure_ascii=False)
            return _image_result(b64, "image/jpeg", f"截图 {w}x{h} 像素。{note}")
        if name == "get_state":
            st = svc.get_state()
            st["cursor"] = _img_cursor(st["cursor"])
            st["coord"] = _coord_block("image")
            return _text_result(json.dumps(st, ensure_ascii=False))
        if name == "zoom":
            coord = _coord_of(args)
            x, y = args.get("x"), args.get("y")
            sx, sy = (_cx(x, coord), _cy(y, coord)) if (x is not None and y is not None) else (None, None)
            jpeg, meta = svc.zoom_tool(sx, sy, args.get("factor", 10), args.get("src", 100))
            model_meta = {
                "seq": meta["seq"], "factor": meta["factor"], "out_size": meta["out_size"],
                "screen_rect": meta["screen_rect"],
                "img_rect": _to_img_rect(meta["screen_rect"]),
                "coord": coord, "img_size": [_img_w, _img_h], "screen_size": [_SCREEN_W, _SCREEN_H],
                "hint": "把放大图里的像素(px,py)交给 zoom_to_screen, 用返回的 img_x/img_y 再调 click/move",
            }
            return _image_result(base64.b64encode(jpeg).decode(), "image/jpeg",
                                 "zoom_meta: " + json.dumps(model_meta, ensure_ascii=False))
        if name == "zoom_to_screen":
            r = svc.zoom_to_screen(args["px"], args["py"])
            r["img_x"], r["img_y"] = _to_img_xy(r["screen_x"], r["screen_y"])
            r.update(_coord_block("image"))
            r["note"] = "click/move 请用 img_x/img_y(image 口径); 画面或截图尺寸变化后请重新 zoom"
            return _text_result(json.dumps(r, ensure_ascii=False))
        if name == "get_input_method":
            return _text_result(json.dumps(svc.get_input_method(args.get("hwnd")),
                                           ensure_ascii=False))
        if name == "switch_input_method":
            return _text_result(json.dumps(
                svc.switch_input_method(args.get("layout", "en"), args.get("hwnd")),
                ensure_ascii=False))
        if name == "list_windows":
            return _text_result(svc.describe_windows(
                title=args.get("title"), wait_seconds=args.get("wait_seconds", 0)))
        if name == "run_actions":
            results = [_run_single(a) for a in args.get("actions", [])]
            return _text_result(json.dumps({"ok": True, "results": results}, ensure_ascii=False))
        if name == "check_vision":
            r = AI.check_vision()
            return _text_result(json.dumps(r, ensure_ascii=False))
        # 其余工具: 包装成动作交给统一执行器(click/move/focus_window/send_text/...)
        r = _run_single(dict(action=name, **args))
        return _text_result(json.dumps(r, ensure_ascii=False))
    except Exception as e:
        _logger.exception("工具执行失败 %s", name)
        return _text_result(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))


def handle_message(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params", {})
        result = handle_tool_call(params.get("name", ""), params.get("arguments", {}))
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if msg_id is not None:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": "Method not found: " + str(method)}}
    return None


def main():
    # Windows 下 Python 的 stdio 默认走 locale 编码(GBK), 而 MCP 客户端按 UTF-8 收发:
    # 不统一就会让"中文工具描述"和"中文参数/返回值"全部乱码(所以 stdin 也要设)。
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except (AttributeError, ValueError, OSError):
        _logger.warning("stdio 重配置为 UTF-8 失败, 中文可能乱码")
    _logger.info("MCP computer server v3 启动(坐标契约 coord=image|screen + %d 个工具)", len(TOOLS))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            resp = handle_message(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError as e:
            _logger.warning("无法解析消息: %s", e)
        except Exception as e:
            _logger.exception("处理消息失败")
            if msg.get("id") is not None:
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0", "id": msg["id"],
                    "error": {"code": -32603, "message": str(e)},
                }) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    main()
