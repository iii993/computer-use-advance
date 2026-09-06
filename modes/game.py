"""游戏模式: 按键宏 + 鼠标直线滑动(随机抖动防检测)"""
import math
import random
import time

from input_engine import keyboard, mouse
from modes.base import BaseMode


class GameMode(BaseMode):
    name = "game"
    display = "游戏模式"

    def __init__(self, app=None):
        super().__init__(app)
        self.cfg = None

    def on_enable(self):
        super().on_enable()
        self.cfg = self.app.config.get("game", {}) if self.app else {}

    # ---------- 基础操作 ----------

    def tap(self, key: str, times: int = 1):
        """连点宏(带间隔抖动)"""
        c = self.cfg
        interval = c.get("tap_interval_ms", 180)
        jitter = c.get("tap_jitter_ms", 30)
        keyboard.tap_repeat(key, max(1, times), interval, jitter)
        self.log.info("连点 %s x%d", key, times)

    def hold(self, key: str, duration_ms: float = 0):
        """按住按键, duration_ms=0 表示按住直到调用 release"""
        if duration_ms <= 0:
            keyboard.press(key)
            self.log.info("按住 %s", key)
            return
        keyboard.press(key)
        time.sleep(duration_ms / 1000.0)
        keyboard.release(key)
        self.log.info("按住 %s %dms", key, duration_ms)

    def release(self, key: str):
        keyboard.release(key)

    # ---------- 直线滑动(带抖动防检测) ----------

    def slide_relative(self, dx: float, dy: float):
        """相对滑动: 目标点 = 当前位置 + (dx, dy), 带高斯抖动与轻微弯曲"""
        c = self.cfg.get("slide", {})
        jitter = c.get("jitter_px", 2.0)
        bend = c.get("curve_bend", 0.06)
        smooth_ms = c.get("smooth_ms", 800)

        x, y = mouse.position()
        tx, ty = x + dx, y + dy

        steps = max(8, int(smooth_ms / 8))
        interval = smooth_ms / steps

        # 轻微弯曲: 垂直于主方向加一个弓形偏移
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length  # 法向量

        for i in range(1, steps + 1):
            t = i / steps
            # 弓形弯曲: 0 -> peak -> 0
            bow = math.sin(math.pi * t) * bend * length
            cx = x + dx * t + nx * bow + random.gauss(0, jitter)
            cy = y + dy * t + ny * bow + random.gauss(0, jitter)
            mouse.move_absolute(cx, cy)
            time.sleep(interval / 1000.0)
        self.log.info("滑动 (%.0f, %.0f) 抖动=%.1f 弯曲=%.2f", dx, dy, jitter, bend)

    def slide_to(self, x: float, y: float):
        """绝对坐标直线滑动"""
        mouse.move_to(x, y, self.cfg.get("mouse", {}))
        self.log.info("滑动到 (%.0f, %.0f)", x, y)

    # ---------- 配置宏执行 ----------

    def run_macro(self, name: str):
        """执行 config 中定义的宏: {type: tap_repeat|hold|slide, ...}"""
        macros = self.cfg.get("macros", {})
        m = macros.get(name)
        if not m:
            self.log.warning("未找到宏: %s", name)
            return
        mtype = m.get("type")
        if mtype == "tap_repeat":
            self.tap(m.get("key", "space"), m.get("times", 3))
        elif mtype == "hold":
            self.hold(m.get("key"), m.get("duration_ms", 300))
        elif mtype == "slide":
            self.slide_relative(m.get("dx", 0), m.get("dy", 0))
        elif mtype == "combo":
            keyboard.combo(m.get("keys", []))
        else:
            self.log.warning("未知宏类型: %s", mtype)
