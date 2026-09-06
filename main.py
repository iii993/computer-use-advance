"""电脑操控插件主程序

三种模式(全局热键切换):
  游戏模式  Ctrl+Shift+1  按键宏 + 抖动直线滑动
  绘画模式  Ctrl+Shift+2  曲线/控制点 + 画笔大小 + 触笔压力
  工作模式  Ctrl+Shift+3  文字输入 + 点击 + 快捷键
  退出      Ctrl+Shift+0
"""
import os
import sys
import threading
import tkinter as tk

# 优先使用项目 vendor 目录的依赖(不依赖全局 site-packages)
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from modes.draw import DrawMode
from modes.game import GameMode
from modes.work import WorkMode
from ui.tray import TrayApp
from utils.config import load_config
from utils.logger import get_logger

log = get_logger("main")


class App:
    def __init__(self, config: dict, tk_root: tk.Tk):
        self.config = config
        self.tk_root = tk_root
        self._lock = threading.Lock()
        self.current = None

        self.modes = {
            "game": GameMode(self),
            "draw": DrawMode(self),
            "work": WorkMode(self),
        }
        self.tray = TrayApp(self)
        self.quit_flag = False

    # ---------- 模式切换 ----------
    def switch_mode(self, name: str):
        with self._lock:
            if self.quit_flag:
                return
            if name not in self.modes:
                log.warning("未知模式: %s", name)
                return
            if self.current and self.current.name == name:
                log.info("已是 %s", name)
                return
            if self.current:
                self.current.on_disable()
            self.current = self.modes[name]
            self.current.on_enable()
            log.info("==> 切换到 %s", self.current.display)

    def current_mode(self):
        with self._lock:
            return self.current

    # ---------- 画布 ----------
    def open_draw_canvas(self):
        self.switch_mode("draw")
        draw = self.modes["draw"]
        draw.open_canvas()

    # ---------- 全局热键 ----------
    def register_hotkeys(self):
        import keyboard as kb

        hk = self.config.get("hotkeys", {})
        kb.add_hotkey(hk.get("game", "<ctrl>+<shift>+1"), lambda: self.switch_mode("game"))
        kb.add_hotkey(hk.get("draw", "<ctrl>+<shift>+2"), lambda: self.switch_mode("draw"))
        kb.add_hotkey(hk.get("work", "<ctrl>+<shift>+3"), lambda: self.switch_mode("work"))
        kb.add_hotkey(hk.get("quit", "<ctrl>+<shift>+0"), self.quit)

        # 游戏模式宏热键
        macros = self.config.get("game", {}).get("macros", {})
        for name, m in macros.items():
            hk_str = m.get("hotkey")
            if hk_str:
                kb.add_hotkey(hk_str, lambda n=name: self._macro_trigger(n))
        log.info("全局热键注册完成: %s", list(hk.values()) + [m.get("hotkey") for m in macros.values()])

    def _macro_trigger(self, name: str):
        with self._lock:
            cur = self.current
        if cur and cur.name == "game":
            cur.run_macro(name)

    # ---------- 生命周期 ----------
    def run(self):
        self.tray.start()
        self.register_hotkeys()
        log.info("=== 电脑操控插件已启动 ===")
        self.tk_root.mainloop()

    def quit(self):
        with self._lock:
            if self.quit_flag:
                return
            self.quit_flag = True
            cur = self.current
        log.info("正在退出…")
        if cur:
            cur.on_disable()
        try:
            import keyboard as kb
            kb.unhook_all()
        except Exception:
            pass
        self.tray.stop()
        self.tk_root.after(100, self.tk_root.destroy)


def main():
    cfg = load_config()
    root = tk.Tk()
    root.withdraw()
    app = App(cfg, root)
    app.run()


if __name__ == "__main__":
    main()
