"""MCP stdio server: 电脑操控能力暴露为 MCP 工具.

坐标约定: 所有坐标参数使用 0-1000 归一化坐标(免疫宿主对截图的任意缩放):
  真实屏幕坐标 = 归一化值 / 1000 × 屏幕尺寸
协议: MCP (JSON-RPC 2.0 over stdio, 每行一条消息). stdout 只输出协议, 日志走文件/stderr.
"""
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


# ---------- 归一化坐标换算(0-1000 -> 屏幕像素) ----------
def _norm_x(v) -> float:
    w, _ = SERVICE.screen_size()
    return float(v) / 1000.0 * w


def _norm_y(v) -> float:
    _, h = SERVICE.screen_size()
    return float(v) / 1000.0 * h


def _norm_point(p) -> tuple:
    return (_norm_x(p[0]), _norm_y(p[1]))


def _norm_cursor(cursor) -> list:
    w, h = SERVICE.screen_size()
    return [round(cursor[0] / w * 1000, 1), round(cursor[1] / h * 1000, 1)]


TOOLS = [
    {"name": "screenshot", "description": "截取全屏图像. 宿主可能缩放图像显示, 坐标请一律用 0-1000 归一化(真实坐标=值/1000×屏幕尺寸).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_state", "description": "获取当前状态(模式/光标[0-1000归一化]/画笔大小).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "click", "description": "点击(x,y为0-1000归一化坐标).",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                                    "clicks": {"type": "integer"}},
                     "required": ["x", "y"]}},
    {"name": "drag", "description": "拖拽: 按住左键从(x1,y1)到(x2,y2), 坐标为0-1000归一化.",
     "inputSchema": {"type": "object",
                     "properties": {"x1": {"type": "number"}, "y1": {"type": "number"},
                                    "x2": {"type": "number"}, "y2": {"type": "number"}},
                     "required": ["x1", "y1", "x2", "y2"]}},
    {"name": "slide", "description": "相对滑动(游戏模式防检测): dx/dy为0-1000归一化相对位移(如100=向右屏幕宽10%).",
     "inputSchema": {"type": "object",
                     "properties": {"dx": {"type": "number"}, "dy": {"type": "number"}},
                     "required": ["dx", "dy"]}},
    {"name": "wait", "description": "等待指定秒数(页面加载/动画).",
     "inputSchema": {"type": "object",
                     "properties": {"seconds": {"type": "number"}},
                     "required": ["seconds"]}},
    {"name": "scroll", "description": "移动到(x,y)[0-1000归一化]并滚轮滚动(dy>0上滚, 每次一格).",
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
    {"name": "mouse_down", "description": "按下左键(x,y归一化, 配合mouse_up画框/拖拽).",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                     "required": ["x", "y"]}},
    {"name": "mouse_up", "description": "释放左键(x,y归一化).",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                     "required": ["x", "y"]}},
    {"name": "set_brush", "description": "设置画笔大小(绘画模式).",
     "inputSchema": {"type": "object",
                     "properties": {"size": {"type": "integer"}}, "required": ["size"]}},
    {"name": "draw_curve", "description": "按控制点绘制平滑曲线(注入外部软件带压力). points为[[x,y],...] 0-1000归一化坐标.",
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
    {"name": "run_actions", "description": "批量执行多个动作(一次决策多次操作, 省往返省token). actions为动作对象数组, 每个对象含 action 字段+该动作参数(同各单工具), 坐标0-1000归一化; 支持 click/double_click/right_click/drag/mouse_down/mouse_up/scroll/type_text/press_key/key_down/key_up/combo/hotkey/wait/slide/switch_mode/set_config/take_screenshot.",
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
            svc.click(_norm_x(action.get("x", 0)), _norm_y(action.get("y", 0)),
                      action.get("button", "left"), int(action.get("clicks", 1)))
        elif act == "double_click":
            svc.click(_norm_x(action["x"]), _norm_y(action["y"]), clicks=2)
        elif act == "right_click":
            svc.click(_norm_x(action["x"]), _norm_y(action["y"]), button="right")
        elif act == "drag":
            svc.drag(_norm_x(action["x1"]), _norm_y(action["y1"]),
                     _norm_x(action["x2"]), _norm_y(action["y2"]))
        elif act == "mouse_down":
            svc.mouse_down(_norm_x(action["x"]), _norm_y(action["y"]))
        elif act == "mouse_up":
            svc.mouse_up(_norm_x(action["x"]), _norm_y(action["y"]))
        elif act == "scroll":
            svc.scroll(_norm_x(action.get("x", 500)), _norm_y(action.get("y", 500)),
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
            w, h = svc.screen_size()
            svc.slide(action.get("dx", 0) / 1000.0 * w,
                      action.get("dy", 0) / 1000.0 * h)
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
            pts = [_norm_point(p) for p in action.get("points", [])]
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
            b64 = svc.screenshot_base64()
            return _image_result(b64, "image/jpeg",
                                 "截图. 注意: 宿主可能缩放图像显示, 所有坐标请用 0-1000 归一化(真实坐标=值/1000×屏幕尺寸)")
        if name == "get_state":
            st = svc.get_state()
            st["cursor"] = _norm_cursor(st["cursor"])
            return _text_result(json.dumps(st, ensure_ascii=False))
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
