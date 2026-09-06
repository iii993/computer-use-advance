"""工作模式: 文字输入 + 简单点击 + 简化快捷键"""
from input_engine import keyboard, mouse, text
from modes.base import BaseMode


class WorkMode(BaseMode):
    name = "work"
    display = "工作模式"

    def __init__(self, app=None):
        super().__init__(app)
        self.cfg = None

    def on_enable(self):
        super().on_enable()
        self.cfg = self.app.config.get("work", {}) if self.app else {}

    # ---------- 文字输入 ----------
    def type_text(self, text: str):
        """输入文字(自然节奏)"""
        tcfg = self.cfg.get("typing", {})
        text.type_text(text, tcfg)
        self.log.info("输入文字: %r", text[:30])

    # ---------- 简单点击 ----------
    def click(self, x=None, y=None, button="left"):
        mouse.click(button, x, y)
        self.log.info("点击 %s at %s", button, (x, y))

    def double_click(self, x=None, y=None):
        mouse.double_click(x, y)

    def right_click(self, x=None, y=None):
        mouse.right_click(x, y)

    def drag(self, start, end):
        mouse.drag(start, end)

    # ---------- 简化快捷键 ----------
    def shortcut(self, name: str):
        """执行预置快捷键(如 copy/paste/undo)"""
        shortcuts = self.cfg.get("shortcuts", {})
        keys = shortcuts.get(name)
        if not keys:
            self.log.warning("未找到快捷键: %s", name)
            return
        keyboard.combo(keys)
        self.log.info("快捷键 %s -> %s", name, keys)

    def press_key(self, key: str):
        keyboard.tap(key)
        self.log.info("按键 %s", key)
