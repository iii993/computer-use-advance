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
        self._uia = None   # UIA 会话(惰性创建; COM STA 要求同线程使用)

    def reload_config(self):
        self.config = load_config()
        self.game.cfg = self.config.get("game", {})
        self.work.cfg = self.config.get("work", {})

    # ---------- 屏幕信息 ----------
    def screen_size(self) -> tuple:
        """当前虚拟屏幕尺寸 (w, h)"""
        return ImageGrab.grab(all_screens=True).size

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

    def screenshot_model(self, max_pixels: int = 640000) -> tuple:
        """截原始图并等比缩到 max_pixels 预算内(默认64万=DSH发送前图像预算),
        返回 (jpeg_bytes, out_w, out_h). 输出尺寸确定, 坐标用图内像素即可准确换算."""
        img = ImageGrab.grab(all_screens=True)
        w, h = img.size
        if w * h > max_pixels:
            scale = (max_pixels / (w * h)) ** 0.5
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            img = img.resize((nw, nh), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        return buf.getvalue(), img.width, img.height

    def screenshot_base64(self, scale=None, quality=None) -> str:
        return base64.b64encode(self.screenshot(scale, quality)).decode()

    # ---------- 鼠标 ----------
    def move(self, x=None, y=None, mode: str = "smooth", duration_ms=None,
             points=None, gap_ms: float = 0) -> dict:
        """只移动, 不点击。单点(x/y)或序列(points)二选一, gap_ms 为序列步间间隔。

        - duration_ms: 整段移动耗时(仅 mode="smooth" 生效)
        - points: [[x, y], [x, y, duration_ms], ...]; 三元点的第三个数覆盖本点耗时
        返回执行记录 {"count", "points", "gap_ms", "duration_ms"}。
        """
        mouse.move(x, y, mode=mode, duration_ms=duration_ms,
                   points=points, gap_ms=gap_ms)
        pts = [[p[0], p[1]] for p in points] if points else ([[x, y]] if x is not None else [])
        log.info("move %s -> %s gap=%sms", mode, pts, gap_ms)
        return {"count": len(pts), "points": pts, "gap_ms": gap_ms,
                "duration_ms": duration_ms}

    def click(self, x=None, y=None, button: str = "left", clicks: int = 1,
              hold_ms: float = 0, interval_ms: float | None = None,
              move_mode: str = "instant", points=None, gap_ms: float = 0,
              duration_ms=None) -> dict:
        """点击。给 points 走序列; 给 x/y 先定位(move_mode=instant 瞬移 / smooth 平滑)再点; 都不给则原地点击。

        - hold_ms: 按压时长(毫秒, 0~5000)
        - interval_ms: 双击间隔(同一次点击内部); gap_ms: 序列相邻两点间歇(两者含义不同)
        返回执行记录 {"count", "points", "gap_ms", "hold_ms", "button"}。
        """
        if move_mode not in ("instant", "smooth"):
            raise ValueError(f"未知 move_mode: {move_mode} (可选 instant/smooth)")
        if gap_ms < 0:
            raise ValueError(f"gap_ms 必须 >= 0, 收到 {gap_ms}")
        if points is not None and (x is not None or y is not None):
            raise ValueError("points 与 x/y 互斥, 只能给一个")
        if (x is None) != (y is None):
            raise ValueError("x 与 y 必须同时提供(只给一个会点错位置)")

        if points is not None:
            mouse.click(button=button, hold_ms=hold_ms, clicks=clicks,
                        interval_ms=interval_ms, points=points, gap_ms=gap_ms)
            pts = [[p[0], p[1]] for p in points]
            log.info("click seq %s hold=%sms gap=%sms", pts, hold_ms, gap_ms)
            return {"count": len(pts), "points": pts, "gap_ms": gap_ms,
                    "hold_ms": hold_ms, "button": button}

        at = None
        if x is not None and y is not None:
            if move_mode == "smooth":
                # 移动与点击独立: 平滑段单独调用, duration_ms 控制整段耗时
                mouse.move(x, y, mode="smooth", duration_ms=duration_ms)
            else:
                at = (x, y)                         # 旧行为: 点击内部瞬移
        mouse.click(button=button, hold_ms=hold_ms, clicks=clicks,
                    interval_ms=interval_ms, at=at, points=None, gap_ms=gap_ms)
        log.info("click %s hold=%sms clicks=%d at=%s", button, hold_ms, clicks, at)
        pts = [[x, y]] if at else []
        return {"count": len(pts), "points": pts, "gap_ms": gap_ms,
                "hold_ms": hold_ms, "button": button}

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
        from input_engine import text as text_engine
        text_engine.type_text(text, tcfg)

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

    # ---------- 观察通道(放大镜 / UIA) ----------
    def zoom_tool(self, x=None, y=None, factor: int = 10, src: int = 100):
        """放大观察指定屏幕坐标(或当前位置); 返回 (jpeg_bytes, meta)。

        meta 含 screen_rect/factor/out_size/seq; 本次映射会被记为"最近一次观察",
        供 zoom_to_screen 反算(见计划 §2.3.1)。
        """
        from computer_core import observe
        b, meta = observe.zoom(x, y, factor=int(factor), src=int(src))
        log.info("zoom factor=%s src=%s rect=%s seq=%s", meta["factor"], src,
                 meta["screen_rect"], meta["seq"])
        return b, meta

    def zoom_to_screen(self, px, py) -> dict:
        """放大图像素 -> 屏幕真实坐标(用最近一次 zoom 的映射)。"""
        from computer_core import observe
        sx, sy = observe.px_to_screen(px, py)
        meta = observe.last_meta() or {}
        log.info("zoom_to_screen (%s,%s) -> (%s,%s) seq=%s", px, py, sx, sy, meta.get("seq"))
        return {"screen_x": sx, "screen_y": sy,
                "seq": meta.get("seq"), "factor": meta.get("factor")}

    def _uia_session(self):
        """惰性创建 UIA 会话(COM STA: 必须在同一线程内使用)。"""
        if self._uia is None:
            from computer_core.uia import UIA
            self._uia = UIA()
        return self._uia

    def describe_windows(self, max_items: int = 30) -> str:
        """列出可见顶层窗口(UIA 无障碍树, 文本)。"""
        return self._uia_session().format_windows(max_items)

    def focus_window(self, hwnd=None, title=None) -> dict:
        """把指定窗口激活到前台(按 hwnd 或标题子串)。返回 {ok, hwnd, name, class, rect}。"""
        if hwnd is None and not title:
            raise ValueError("focus_window 需要 hwnd 或 title 之一")
        u = self._uia_session()
        rec, others = u.find_window(hwnd=hwnd, title=title, with_candidates=True)
        if rec is None:
            cands = [f"{w.get('hwnd')}:{w.get('name') or w.get('win32_title')}"
                     for w in u.list_windows()[:10]]
            return {"ok": False,
                    "error": f"未找到窗口 (hwnd={hwnd}, title={title!r}); 当前候选: {cands}"}
        ok = u.focus(rec["hwnd"])
        log.info("focus_window hwnd=%s title=%r -> ok=%s", rec["hwnd"], title, ok)
        out = {"ok": bool(ok), "hwnd": rec["hwnd"],
               "name": rec.get("name") or rec.get("win32_title"),
               "class": rec.get("class"), "rect": rec.get("rect")}
        if others:
            # 有歧义时明确告知: 调用方应改用 hwnd 精确指定
            out["other_candidates"] = others
            out["note"] = ("标题匹配到多个窗口, 已选最精确的一个; "
                           "需要精确指定请用 list_windows 拿到 hwnd 再调用")
        if not ok:
            out["note"] = ("窗口已定位但未能置前(可能受 Windows 前台锁定或权限限制); "
                           "可直接用 rect 算出中心点坐标后 click 该区域")
        return out

    def send_text(self, text: str, submit_keys: list | None = None,
                  clear_first: bool = False) -> dict:
        """向当前聚焦的控件输入文字, 可选地按组合键提交。

        - text: 要输入的文字(中文/长文本走剪贴板, 见 input_engine.text)
        - submit_keys: 提交组合键, 如 ["ctrl", "enter"]; 很多应用里单独 enter 只换行
        - clear_first: 先 Ctrl+A 全选再删除, 清掉输入框里的旧内容
        """
        tcfg = self.config.get("work", {}).get("typing", {})
        from input_engine import text as text_engine
        if clear_first:
            keyboard.combo(["ctrl", "a"])
            keyboard.tap("delete")
        text_engine.type_text(text or "", tcfg)
        if submit_keys:
            keyboard.combo(list(submit_keys))
        log.info("send_text len=%d submit=%s clear_first=%s",
                 len(text or ""), submit_keys, clear_first)
        return {"ok": True, "text_len": len(text or ""),
                "submit_keys": list(submit_keys) if submit_keys else None}

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
