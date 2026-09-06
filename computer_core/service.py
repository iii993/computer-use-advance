"""操控服务: 统一封装屏幕/鼠标/键盘/绘画/模式/配置能力, 供 AI 循环与 MCP 调用"""
import base64
import io
import os
import sys

# vendor 依赖优先
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENDOR = os.path.join(_BASE, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from PIL import Image, ImageGrab

from input_engine import keyboard, mouse
from input_engine.pen import PenInjector, PressureMapper
from modes.game import GameMode
from modes.work import WorkMode
from utils.config import CONFIG_PATH, load_config, save_config
from utils.logger import get_logger

log = get_logger("core")


class ComputerService:
    """电脑操控服务: 所有能力的统一入口"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self._cfg_cache = None
        # 模式实例(不依赖 tk 的轻量模式)
        self.game = GameMode(self)
        self.work = WorkMode(self)
        self.game.cfg = self.config.get("game", {})
        self.work.cfg = self.config.get("work", {})
        self.injector = PenInjector()
        self.mapper = PressureMapper(self.config.get("draw", {}).get("pen", {}))
        self.mode = "work"  # 当前模式
        self.brush_size = int(self.config.get("draw", {}).get("brush", {}).get("min", 1))

    def reload_config(self):
        self.config = load_config()
        self.game.cfg = self.config.get("game", {})
        self.work.cfg = self.config.get("work", {})

    # ---------- 截图 ----------
    def screenshot(self, scale: float | None = None, quality: int | None = None) -> bytes:
        """全屏截图 -> JPEG bytes(缩放压缩降 token)"""
        ai_cfg = self.config.get("ai", {})
        scale = scale if scale is not None else float(ai_cfg.get("screenshot_scale", 0.6))
        quality = quality if quality is not None else int(ai_cfg.get("screenshot_quality", 60))
        img = ImageGrab.grab(all_screens=True)
        if scale and 0 < scale < 1.0:
            # 仅当显式配置缩放时压缩(默认 1.0 保持原始分辨率, 坐标与屏幕一致)
            w, h = img.size
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def screenshot_png(self) -> bytes:
        """PNG 原图(供 MCP 展示)"""
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()

    def screenshot_base64(self, scale=None, quality=None) -> str:
        return base64.b64encode(self.screenshot(scale, quality)).decode()

    # ---------- 鼠标 ----------
    def move(self, x: float, y: float):
        mouse.move_absolute(x, y)

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1):
        if clicks > 1:
            mouse.move_absolute(x, y)
            for _ in range(clicks):
                mouse.mc.click(mouse._btn(button), 1)
        else:
            mouse.click(button, x, y)
        log.info("click %s(%d) @ (%.0f, %.0f)", button, clicks, x, y)

    def drag(self, x1, y1, x2, y2, duration_ms: float = 400):
        mouse.drag((x1, y1), (x2, y2), duration_ms)
        log.info("drag (%.0f,%.0f)->(%.0f,%.0f)", x1, y1, x2, y2)

    def slide(self, dx: float, dy: float):
        """游戏模式: 抖动直线滑动"""
        self.game.slide_relative(dx, dy)

    # ---------- 键盘 ----------
    def press_key(self, key: str):
        keyboard.tap(key)

    def key_down(self, key: str):
        keyboard.press(key)

    def key_up(self, key: str):
        keyboard.release(key)

    def hotkey(self, keys: list):
        keyboard.combo(keys)

    def combo(self, keys: list):
        keyboard.combo(keys)

    # ---------- 高级鼠标 ----------
    def wait(self, seconds: float):
        """等待(页面加载/动画完成)"""
        import time
        time.sleep(max(0.0, float(seconds)))

    def scroll(self, x: float, y: float, dx: float = 0, dy: float = -300):
        """移动到(x,y)并滚轮滚动(dy>0上滚, dy<0下滚)"""
        if x is not None and y is not None:
            mouse.move_absolute(x, y)
        mouse.mc.scroll(float(dx), float(dy))
        log.info("scroll at (%.0f,%.0f) delta=(%.0f,%.0f)", x or 0, y or 0, dx, dy)

    def mouse_down(self, x: float, y: float, button: str = "left"):
        mouse.move_absolute(x, y)
        mouse.mc.press(mouse._btn(button))

    def mouse_up(self, x: float, y: float, button: str = "left"):
        mouse.mc.release(mouse._btn(button))

    def type_text(self, text: str):
        tcfg = self.config.get("work", {}).get("typing", {})
        from input_engine import text
        text.type_text(text, tcfg)

    # ---------- 绘画 ----------
    def set_brush(self, n: int):
        b = self.config.get("draw", {}).get("brush", {})
        n = max(int(b.get("min", 1)), min(int(b.get("max", 200)), int(n)))
        self.brush_size = n
        log.info("画笔大小 -> %d", n)
        return n

    def draw_curve(self, points: list, inject: bool = True):
        """控制点列表 -> 中点贝塞尔曲线重放(带压力注入或鼠标拖动)"""
        if len(points) < 2:
            return {"ok": False, "error": "至少需要2个控制点"}
        curve = mouse.midpoint_bezier_curve([tuple(p) for p in points],
                                            segments=int(self.config.get("draw", {}).get("bezier_segments", 8)))
        interval = float(self.config.get("draw", {}).get("replay_interval_ms", 8))
        if inject:
            # 笔通道(压力)
            self.mapper.reset()
            pressures = [self.mapper.update(p) for p in curve]
            self.injector.pen_down(*curve[0], pressure=pressures[0])
            try:
                for i, pt in enumerate(curve[1:], start=1):
                    self.injector.pen_move(*pt, pressure=pressures[i])
                    import time
                    time.sleep(interval / 1000.0)
            finally:
                self.injector.pen_up(*curve[-1])
        else:
            # 鼠标拖动通道
            import time
            mouse.move_absolute(*curve[0])
            mouse.mc.press(mouse.Button.left)
            try:
                for pt in curve[1:]:
                    mouse.move_absolute(*pt)
                    time.sleep(interval / 1000.0)
            finally:
                mouse.mc.release(mouse.Button.left)
        log.info("绘制曲线 %d 控制点 -> %d 细分点", len(points), len(curve))
        return {"ok": True, "points": len(curve)}

    # ---------- 模式 ----------
    def switch_mode(self, mode: str):
        if mode not in ("game", "draw", "work"):
            return {"ok": False, "error": f"未知模式: {mode}, 可选 game/draw/work"}
        self.mode = mode
        if mode == "game":
            self.game.on_enable()
        elif mode == "work":
            self.work.on_enable()
        # draw 模式依赖 tk, 轻量场景只标记
        log.info("模式切换 -> %s", mode)
        return {"ok": True, "mode": mode}

    def get_state(self) -> dict:
        """当前状态(给模型观察)"""
        x, y = mouse.position()
        return {
            "mode": self.mode,
            "cursor": [round(x, 0), round(y, 0)],
            "brush_size": self.brush_size,
            "pen_channel": False,
        }

    # ---------- 配置(白名单) ----------
    def _resolve_key(self, dotted: str, cfg: dict):
        parts = dotted.split(".")
        node = cfg
        for p in parts[:-1]:
            node = node.get(p, {})
            if not isinstance(node, dict):
                return None
        return node, parts[-1]

    def set_config(self, dotted: str, value):
        """白名单内修改配置, 立即写入 config.json"""
        whitelist = self.config.get("ai", {}).get("whitelist", [])
        if dotted not in whitelist:
            return {"ok": False, "error": f"键不在白名单: {dotted} (白名单: {whitelist})"}
        node, key = self._resolve_key(dotted, self.config)
        if node is None or key not in node:
            return {"ok": False, "error": f"配置键不存在: {dotted}"}
        try:
            # 类型保持(数字/字符串/列表)
            if isinstance(node[key], bool):
                node[key] = bool(value)
            elif isinstance(node[key], (int, float)):
                node[key] = type(node[key])(value)
            else:
                node[key] = value
            save_config(self.config)
            log.info("配置修改: %s = %r", dotted, node[key])
            return {"ok": True, "key": dotted, "value": node[key]}
        except (ValueError, TypeError) as e:
            return {"ok": False, "error": f"值不合法: {e}"}

    def get_config(self, dotted: str | None = None):
        if not dotted:
            return self.config
        node, key = self._resolve_key(dotted, self.config)
        return node.get(key) if node and key else None
