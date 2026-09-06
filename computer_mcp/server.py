"""MCP stdio server: 把电脑操控能力暴露为 MCP 工具, 供 DSH agent 调用.

协议: MCP (JSON-RPC 2.0 over stdio, 每行一条消息).
注意: stdout 只输出协议消息; 日志走 stderr/文件, 不污染协议流.
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

# 日志只走文件 + stderr(不污染 stdout 协议)
from utils.logger import setup_logger, _LOG_DIR
_logger = setup_logger("mcp")
for h in list(_logger.handlers):
    if hasattr(h, "stream") and getattr(h.stream, "name", "") == "<stdout>":
        _logger.removeHandler(h)

from computer_core.service import ComputerService
from ai_mode.controller import AIController

SERVICE = ComputerService()
AI = AIController(SERVICE)

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "computer", "version": "1.0.0"}

TOOLS = [
    {"name": "screenshot", "description": "截取全屏图像(JPEG压缩), 返回图像与缩放比. 模型观察屏幕用.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_state", "description": "获取当前状态(模式/光标位置/画笔大小).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "click", "description": "点击屏幕坐标(基于原始屏幕分辨率).",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                                    "clicks": {"type": "integer"}},
                     "required": ["x", "y"]}},
    {"name": "drag", "description": "拖拽: 按住左键从(x1,y1)到(x2,y2).",
     "inputSchema": {"type": "object",
                     "properties": {"x1": {"type": "number"}, "y1": {"type": "number"},
                                    "x2": {"type": "number"}, "y2": {"type": "number"}},
                     "required": ["x1", "y1", "x2", "y2"]}},
    {"name": "slide", "description": "相对滑动(游戏模式, 带防检测抖动/弯曲).",
     "inputSchema": {"type": "object",
                     "properties": {"dx": {"type": "number"}, "dy": {"type": "number"}},
                     "required": ["dx", "dy"]}},
    {"name": "wait", "description": "等待指定秒数(页面加载/动画完成).",
     "inputSchema": {"type": "object",
                     "properties": {"seconds": {"type": "number"}},
                     "required": ["seconds"]}},
    {"name": "scroll", "description": "移动到(x,y)并滚轮滚动(dy>0上滚, dy<0下滚).",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                    "dy": {"type": "number"}},
                     "required": ["dy"]}},
    {"name": "type_text", "description": "输入文字(中文自动走剪贴板, 自然节奏).",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "press_key", "description": "单击按键(space/enter/tab/esc/f1-f12/ctrl/alt 等).",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "combo", "description": "组合键, 例如 ctrl+c (按顺序按下再逆序释放).",
     "inputSchema": {"type": "object",
                     "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
                     "required": ["keys"]}},
    {"name": "set_brush", "description": "设置画笔大小(绘画模式).",
     "inputSchema": {"type": "object",
                     "properties": {"size": {"type": "integer"}}, "required": ["size"]}},
    {"name": "draw_curve", "description": "按控制点绘制平滑曲线(注入外部软件, 带压力模拟).",
     "inputSchema": {"type": "object",
                     "properties": {"points": {"type": "array",
                                               "items": {"type": "array", "items": {"type": "number"}}},
                                    "inject": {"type": "boolean"}},
                     "required": ["points"]}},
    {"name": "switch_mode", "description": "切换模式: game(游戏)/draw(绘画)/work(工作).",
     "inputSchema": {"type": "object",
                     "properties": {"mode": {"type": "string", "enum": ["game", "draw", "work"]}},
                     "required": ["mode"]}},
    {"name": "set_config", "description": "修改配置(仅白名单内的键, 立即写入config.json).",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}, "value": {}},
                     "required": ["key", "value"]}},
    {"name": "check_vision", "description": "检测当前配置的模型是否支持识图(发测试图, 不支持会报错).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "run_ai_task", "description": "执行完整AI任务循环: 截图->模型决策->动作, 直到完成.",
     "inputSchema": {"type": "object",
                     "properties": {"task": {"type": "string"},
                                    "max_steps": {"type": "integer"}},
                     "required": ["task"]}},
]


def _text_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _image_result(b64: str, mime: str = "image/jpeg", note: str = "") -> dict:
    content = [{"type": "image", "data": b64, "mimeType": mime}]
    if note:
        content.append({"type": "text", "text": note})
    return {"content": content}


def handle_tool_call(name: str, args: dict) -> dict:
    svc = SERVICE
    try:
        if name == "screenshot":
            b64 = svc.screenshot_base64()
            return _image_result(b64, "image/jpeg",
                                 "截图(原始分辨率, 坐标与真实屏幕一致, 直接使用)")
        if name == "get_state":
            return _text_result(json.dumps(svc.get_state(), ensure_ascii=False))
        if name == "click":
            svc.click(args.get("x", 0), args.get("y", 0),
                      args.get("button", "left"), int(args.get("clicks", 1)))
            return _text_result("ok")
        if name == "drag":
            svc.drag(args["x1"], args["y1"], args["x2"], args["y2"])
            return _text_result("ok")
        if name == "slide":
            svc.slide(args.get("dx", 0), args.get("dy", 0))
            return _text_result("ok")
        if name == "wait":
            svc.wait(args.get("seconds", 1))
            return _text_result("ok")
        if name == "scroll":
            svc.scroll(args.get("x"), args.get("y"),
                       args.get("dx", 0), args.get("dy", -300))
            return _text_result("ok")
        if name == "type_text":
            svc.type_text(args.get("text", ""))
            return _text_result("ok")
        if name == "press_key":
            svc.press_key(args.get("key", ""))
            return _text_result("ok")
        if name == "combo":
            svc.combo(args.get("keys", []))
            return _text_result("ok")
        if name == "set_brush":
            n = svc.set_brush(args.get("size", 1))
            return _text_result("画笔大小: " + str(n))
        if name == "draw_curve":
            r = svc.draw_curve(args.get("points", []), args.get("inject", True))
            return _text_result(json.dumps(r, ensure_ascii=False))
        if name == "switch_mode":
            r = svc.switch_mode(args.get("mode", ""))
            return _text_result(json.dumps(r, ensure_ascii=False))
        if name == "set_config":
            r = svc.set_config(args.get("key", ""), args.get("value"))
            return _text_result(json.dumps(r, ensure_ascii=False))
        if name == "check_vision":
            r = AI.check_vision()
            return _text_result(json.dumps(r, ensure_ascii=False))
        if name == "run_ai_task":
            r = AI.run_task(args.get("task", ""), args.get("max_steps"))
            return _text_result(json.dumps(r, ensure_ascii=False))
        return _text_result(json.dumps({"ok": False, "error": "未知工具: " + name}))
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
    _logger.info("MCP computer server 启动")
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
