"""MCP stdio server: 电脑操控能力暴露为 MCP 工具.

坐标约定: 所有坐标参数使用 0-1000 归一化坐标(免疫宿主对截图的任意缩放):
  真实屏幕坐标 = 归一化值 / 1000 × 屏幕尺寸
协议: MCP (JSON-RPC 2.0 over stdio, 每行一条消息). stdout 只输出协议, 日志走文件/stderr.
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
SERVER_INFO = {"name": "computer", "version": "2.0.0"}


# ---------- 截图内像素坐标换算(图内像素 -> 屏幕像素) ----------
# 截图时记录当前输出图尺寸(_img_w/_img_h), 换算系数 = 屏幕尺寸 / 截图尺寸.
_SCREEN_W, _SCREEN_H = SERVICE.screen_size()
_img_w, _img_h = _SCREEN_W, _SCREEN_H  # 初始=屏幕(未截图时1:1)


def _set_img_size(w: int, h: int):
    global _img_w, _img_h
    _img_w, _img_h = max(1, w), max(1, h)


def _img_x(v) -> float:
    return float(v) * _SCREEN_W / _img_w


def _img_y(v) -> float:
    return float(v) * _SCREEN_H / _img_h


def _img_point(p) -> tuple:
    return (_img_x(p[0]), _img_y(p[1]))


def _img_cursor(cursor) -> list:
    return [round(cursor[0] / _SCREEN_W * _img_w, 1),
            round(cursor[1] / _SCREEN_H * _img_h, 1)]


def _img_points(points) -> list:
    """坐标序列 截图内像素 -> 屏幕坐标(保留三元点的第三个值)。"""
    out = []
    for p in points or []:
        pt = [_img_x(p[0]), _img_y(p[1])]
        if len(p) > 2:
            pt.append(p[2])
        out.append(pt)
    return out


def _to_img_xy(sx, sy) -> list:
    """屏幕坐标 -> 截图内像素(模型侧口径, 供 click/move 直接使用)。"""
    return [round(sx / _SCREEN_W * _img_w, 1),
            round(sy / _SCREEN_H * _img_h, 1)]


def _to_img_rect(rect) -> list:
    """屏幕矩形 -> 截图内像素矩形。"""
    return [_to_img_xy(rect[0], rect[1])[0], _to_img_xy(rect[0], rect[1])[1],
            _to_img_xy(rect[2], rect[3])[0], _to_img_xy(rect[2], rect[3])[1]]


TOOLS = [
    {"name": "screenshot", "description": "截取全屏图像(等比缩到约64万像素, 尺寸见返回说明). 坐标请用【截图内像素坐标】: x范围0-截图宽, y范围0-截图高, server自动换算真实屏幕.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_state", "description": "获取当前状态(模式/光标[截图内像素]/画笔大小).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "click", "description": "点击(x,y为截图内像素坐标, 以最近一次截图尺寸为准; 不给 x/y 则原地点击). 支持 hold_ms 按压时长、points 坐标序列与 gap_ms 步间间隔.",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "button": {"type": "string", "enum": ["left", "right", "middle", "x1", "x2"]},
                                    "clicks": {"type": "integer"},
                                    "hold_ms": {"type": "number", "description": "按压时长(毫秒), 0~5000, 默认 0"},
                                    "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                                               "description": "坐标序列 [[x,y], [x,y,hold_ms], ...], 与 x/y 互斥; 每个点都会点击"},
                                    "gap_ms": {"type": "number", "description": "序列相邻两点的间歇(毫秒), 默认 0; 与双击间隔无关"},
                                    "move_mode": {"type": "string", "enum": ["instant", "smooth"],
                                                  "description": "给了 x/y 时的定位方式: instant 瞬移(默认) / smooth 平滑"},
                                    "duration_ms": {"type": "number", "description": "move_mode=smooth 时的移动耗时(毫秒)"}}}},
    {"name": "drag", "description": "拖拽: 按住左键从(x1,y1)到(x2,y2), 坐标为截图内像素.",
     "inputSchema": {"type": "object",
                     "properties": {"x1": {"type": "number"}, "y1": {"type": "number"},
                                    "x2": {"type": "number"}, "y2": {"type": "number"}},
                     "required": ["x1", "y1", "x2", "y2"]}},
    {"name": "slide", "description": "相对滑动(游戏模式防检测): dx/dy为截图内像素相对位移.",
     "inputSchema": {"type": "object",
                     "properties": {"dx": {"type": "number"}, "dy": {"type": "number"}},
                     "required": ["dx", "dy"]}},
    {"name": "wait", "description": "等待指定秒数(页面加载/动画).",
     "inputSchema": {"type": "object",
                     "properties": {"seconds": {"type": "number"}},
                     "required": ["seconds"]}},
    {"name": "scroll", "description": "移动到(x,y)[截图内像素]并滚轮滚动(dy>0上滚, 每次一格).",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "dy": {"type": "number"}},
                     "required": ["dy"]}},
    {"name": "type_text", "description": "输入文字(中文自动剪贴板).",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "press_key", "description": "单击按键(space/enter/tab/esc/f1-f12/ctrl/alt 等).",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "key_down", "description": "按住按键(配合key_up长按).",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "key_up", "description": "释放按键.",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "combo", "description": "组合键(如 [ctrl,c]).",
     "inputSchema": {"type": "object",
                     "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
                     "required": ["keys"]}},
    {"name": "mouse_down", "description": "按下左键(x,y截图内像素, 配合mouse_up画框/拖拽).",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                     "required": ["x", "y"]}},
    {"name": "mouse_up", "description": "释放左键(x,y截图内像素).",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                     "required": ["x", "y"]}},
    {"name": "set_brush", "description": "设置画笔大小(绘画模式).",
     "inputSchema": {"type": "object",
                     "properties": {"size": {"type": "integer"}}, "required": ["size"]}},
    {"name": "draw_curve", "description": "按控制点绘制平滑曲线(注入外部软件带压力). points为[[x,y],...] 截图内像素坐标.",
     "inputSchema": {"type": "object",
                     "properties": {"points": {"type": "array",
                                               "items": {"type": "array", "items": {"type": "number"}}},
                                    "inject": {"type": "boolean"}},
                     "required": ["points"]}},
    {"name": "switch_mode", "description": "切换模式: game/draw/work.",
     "inputSchema": {"type": "object",
                     "properties": {"mode": {"type": "string", "enum": ["game", "draw", "work"]}},
                     "required": ["mode"]}},
    {"name": "set_config", "description": "修改配置(仅白名单, 立即写入config.json).",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}, "value": {}},
                     "required": ["key", "value"]}},
    {"name": "check_vision", "description": "检测MCP服务端配置的模型是否支持识图.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "move", "description": "只移动鼠标, 不点击(坐标=截图内像素). 单点给 x/y(可选 duration_ms 设整段耗时); 序列给 points=[[x,y],[x,y,duration_ms],...] 并用 gap_ms 设步间间隔. mode=smooth 平滑/instant 瞬移.",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "mode": {"type": "string", "enum": ["smooth", "instant"]},
                                    "duration_ms": {"type": "number", "description": "单次移动耗时(毫秒), 仅 smooth 生效"},
                                    "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                                               "description": "坐标序列 [[x,y], [x,y,duration_ms], ...], 与 x/y 互斥"},
                                    "gap_ms": {"type": "number", "description": "序列相邻两点的间歇(毫秒), 默认 0"}}}},
    {"name": "zoom", "description": "放大观察: 以(x,y)[截图内像素]为中心放大一块屏幕区域(默认10X, NEAREST 插值), 返回放大图 + zoom_meta(screen_rect/factor/img_rect/seq). 把放大图里的像素交给 zoom_to_screen 即可换算回坐标.",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "factor": {"type": "integer"}, "src": {"type": "integer"}}}},
    {"name": "zoom_to_screen", "description": "把【最近一次 zoom 图像】里的像素(px,py, 图左上角为0,0)换算成坐标, 返回 screen_x/screen_y 与 img_x/img_y(click/move 直接用后者). 越界或还没 zoom 过会报错; 本工具不重新截图, 画面变化请重新 zoom.",
     "inputSchema": {"type": "object",
                     "properties": {"px": {"type": "number"}, "py": {"type": "number"}},
                     "required": ["px", "py"]}},
    {"name": "list_windows", "description": "列出当前可见顶层窗口(UIA 无障碍树, 文本). 含 hwnd/名称/类名/矩形/控件类型. 注意: 游戏/Canvas/自绘 UI 可能读不出内容, 这类场景请用 screenshot/zoom.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "run_actions", "description": "批量执行多个动作(一次决策多次操作, 省往返省token). actions为动作对象数组, 每个对象含 action 字段+该动作参数(同各单工具), 坐标用截图内像素; 支持 click/double_click/right_click/drag/mouse_down/mouse_up/scroll/type_text/press_key/key_down/key_up/combo/hotkey/wait/slide/move/zoom_to_screen/switch_mode/set_config/take_screenshot.",
     "inputSchema": {"type": "object",
                     "properties": {"actions": {"type": "array",
                                                "items": {"type": "object"}}},
                     "required": ["actions"]}},
]


def _text_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _image_result(b64: str, mime: str = "image/jpeg", note: str = "") -> dict:
    content = [{"type": "image", "data": b64, "mimeType": mime}]
    if note:
        content.append({"type": "text", "text": note})
    return {"content": content}


# ---------- 单动作执行(归一化坐标换算统一入口) ----------
def _run_single(action: dict) -> dict:
    svc = SERVICE
    act = action.get("action") or action.get("name")
    try:
        if act == "click":
            kw = {"button": action.get("button", "left"),
                  "clicks": int(action.get("clicks", 1)),
                  "hold_ms": float(action.get("hold_ms", 0)),
                  "gap_ms": float(action.get("gap_ms", 0)),
                  "move_mode": action.get("move_mode", "instant"),
                  "duration_ms": action.get("duration_ms")}
            if action.get("points"):
                return svc.click(points=_img_points(action["points"]), **kw)
            if action.get("x") is None or action.get("y") is None:
                return svc.click(**kw)                 # 不给坐标 = 原地点击(不能默认 0,0)
            return svc.click(_img_x(action["x"]), _img_y(action["y"]), **kw)
        elif act == "move":
            mode = action.get("mode", "smooth")
            dur = action.get("duration_ms")
            if action.get("points"):
                return svc.move(points=_img_points(action["points"]),
                                gap_ms=float(action.get("gap_ms", 0)),
                                mode=mode, duration_ms=dur)
            return svc.move(_img_x(action.get("x", 0)), _img_y(action.get("y", 0)),
                            mode=mode, duration_ms=dur)
        elif act == "zoom_to_screen":
            r = svc.zoom_to_screen(action["px"], action["py"])
            r["img_x"], r["img_y"] = _to_img_xy(r["screen_x"], r["screen_y"])
            r["note"] = "click/move 请用 img_x/img_y(截图内像素)"
            return r
        elif act == "double_click":
            svc.click(_img_x(action["x"]), _img_y(action["y"]), clicks=2)
        elif act == "right_click":
            svc.click(_img_x(action["x"]), _img_y(action["y"]), button="right")
        elif act == "drag":
            svc.drag(_img_x(action["x1"]), _img_y(action["y1"]),
                     _img_x(action["x2"]), _img_y(action["y2"]))
        elif act == "mouse_down":
            svc.mouse_down(_img_x(action["x"]), _img_y(action["y"]))
        elif act == "mouse_up":
            svc.mouse_up(_img_x(action["x"]), _img_y(action["y"]))
        elif act == "scroll":
            svc.scroll(_img_x(action.get("x", _img_w // 2)), _img_y(action.get("y", _img_h // 2)),
                       action.get("dx", 0), action.get("dy", -1))
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
        elif act == "slide":
            svc.slide(_img_x(action.get("dx", 0)), _img_y(action.get("dy", 0)))
        elif act == "wait":
            svc.wait(action.get("seconds", 1))
        elif act == "switch_mode":
            return svc.switch_mode(action.get("mode", ""))
        elif act == "set_config":
            return svc.set_config(action.get("key", ""), action.get("value"))
        elif act == "set_brush":
            n = svc.set_brush(action.get("size", 1))
            return {"ok": True, "brush": n}
        elif act == "draw_curve":
            pts = [_img_point(p) for p in action.get("points", [])]
            return svc.draw_curve(pts, action.get("inject", True))
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
            _set_img_size(w, h)
            b64 = base64.b64encode(img_bytes).decode()
            return _image_result(b64, "image/jpeg",
                                 "截图尺寸: " + str(w) + "x" + str(h) + " 像素. 坐标请用截图内像素(0-" + str(w) + ", 0-" + str(h) + "), 无需自己换算")
        if name == "get_state":
            st = svc.get_state()
            st["cursor"] = _img_cursor(st["cursor"])
            return _text_result(json.dumps(st, ensure_ascii=False))
        if name == "zoom":
            x, y = args.get("x"), args.get("y")
            sx, sy = (_img_x(x), _img_y(y)) if (x is not None and y is not None) else (None, None)
            jpeg, meta = svc.zoom_tool(sx, sy, args.get("factor", 10), args.get("src", 100))
            model_meta = {
                "seq": meta["seq"], "factor": meta["factor"],
                "out_size": meta["out_size"],
                "screen_rect": meta["screen_rect"],
                "img_rect": _to_img_rect(meta["screen_rect"]),
                "hint": "把放大图里的像素(px,py)交给 zoom_to_screen, 用它返回的 img_x/img_y 再调 click/move",
            }
            return _image_result(base64.b64encode(jpeg).decode(), "image/jpeg",
                                 "zoom_meta: " + json.dumps(model_meta, ensure_ascii=False))
        if name == "zoom_to_screen":
            r = svc.zoom_to_screen(args["px"], args["py"])
            r["img_x"], r["img_y"] = _to_img_xy(r["screen_x"], r["screen_y"])
            r["img_size"] = [_img_w, _img_h]
            r["note"] = ("img_x/img_y 是 img_size 口径的截图坐标; 画面或截图尺寸变化后请重新 zoom")
            return _text_result(json.dumps(r, ensure_ascii=False))
        if name == "list_windows":
            return _text_result(svc.describe_windows())
        if name == "run_actions":
            results = [_run_single(a) for a in args.get("actions", [])]
            return _text_result(json.dumps({"ok": True, "results": results}, ensure_ascii=False))
        if name == "check_vision":
            r = AI.check_vision()
            return _text_result(json.dumps(r, ensure_ascii=False))
        # 其余工具: 包装成动作交给统一执行器
        r = _run_single(dict(action=name, **args))
        return _text_result(json.dumps(r, ensure_ascii=False))
    except Exception as e:
        _logger.exception("工具执行失败 %s", name)
        return _text_result(json.dumps({"ok": False, "error": str(e)}))


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
    _logger.info("MCP computer server v2 启动(归一化坐标+run_actions)")
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
