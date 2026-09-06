"""系统托盘图标"""
import threading

from utils.logger import get_logger

log = get_logger("tray")


class TrayApp:
    def __init__(self, app):
        self.app = app
        self.icon = None
        self._thread = None

    def _build_menu(self):
        import pystray
        from pystray import Menu, MenuItem

        def make_switch(name):
            def act(icon, item):
                self.app.switch_mode(name)
            return act

        def open_canvas(icon, item):
            self.app.open_draw_canvas()

        def open_ai(icon, item):
            self.app.open_ai_task()

        def quit_app(icon, item):
            self.app.quit()

        return Menu(
            MenuItem("🎮 游戏模式", make_switch("game")),
            MenuItem("🎨 绘画模式", make_switch("draw")),
            MenuItem("💼 工作模式", make_switch("work")),
            Menu.SEPARATOR,
            MenuItem("🖼 打开绘画画布", open_canvas),
            MenuItem("🤖 AI 任务", open_ai),
            Menu.SEPARATOR,
            MenuItem("退出", quit_app),
        )

    def _create_icon(self):
        import pystray
        from PIL import Image, ImageDraw

        # 生成一个 64x64 的简单图标(蓝底白鼠标)
        img = Image.new("RGB", (64, 64), "#2563eb")
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([14, 20, 50, 48], radius=6, fill="white")
        d.polygon([(24, 20), (24, 10), (30, 18), (34, 12), (38, 18)], fill="white")
        d.rectangle([30, 30, 38, 44], fill="#2563eb")
        return img

    def start(self):
        import pystray
        self.icon = pystray.Icon("ccp", self._create_icon(), "电脑操控插件", menu=self._build_menu())
        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()
        log.info("托盘已启动")

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
