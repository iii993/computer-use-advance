"""绘画模式: 曲线绘制(控制点系统) + 画笔大小 + 触笔压力注入"""
import threading
import time

from input_engine import mouse
from input_engine.pen import PenInjector, PressureMapper
from modes.base import BaseMode


class DrawMode(BaseMode):
    name = "draw"
    display = "绘画模式"

    def __init__(self, app=None):
        super().__init__(app)
        self.cfg = None
        self.brush_size = 6
        self.pen_channel = False          # False=鼠标通道, True=笔(压力)通道
        self.injector = PenInjector()
        self.mapper = None
        self.canvas = None
        self.overlay = None
        self._listeners = []
        self._root = None

    def on_enable(self):
        super().on_enable()
        self.cfg = self.app.config.get("draw", {}) if self.app else {}
        self.mapper = PressureMapper(self.cfg.get("pen", {}))
        self.brush_size = int(self.cfg.get("brush", {}).get("min", 1))

        # 主线程创建 tk 窗口(overlay 需要)
        self._root = self.app.tk_root if self.app else None

        # 启动输入监听(滚轮调画笔 + P 键切换通道)
        self._start_listeners()

    def on_disable(self):
        super().on_disable()
        self._stop_listeners()
        if self.injector.is_active:
            self.injector.cancel()
        if self.overlay:
            self.overlay.hide()

    # ---------- 监听器 ----------
    def _start_listeners(self):
        try:
            from pynput import mouse as pmouse, keyboard as pkey

            def on_scroll(x, y, dx, dy):
                try:
                    from pynput.keyboard import Controller as KbCtrl
                    # Alt 按下时调整画笔
                    if KbCtrl().alt_pressed:
                        step = int(self.cfg.get("brush", {}).get("step", 2))
                        self.change_brush(step if dy > 0 else -step)
                except Exception:
                    pass

            def on_key(key):
                try:
                    toggle = self.cfg.get("pen", {}).get("toggle_key", "p")
                    if hasattr(key, "char") and key.char and key.char.lower() == toggle.lower():
                        self.toggle_channel()
                except Exception:
                    pass

            self._m_listener = pmouse.Listener(on_scroll=on_scroll)
            self._k_listener = pkey.Listener(on_press=on_key)
            self._m_listener.daemon = True
            self._k_listener.daemon = True
            self._m_listener.start()
            self._k_listener.start()
            self._listeners = [self._m_listener, self._k_listener]
        except Exception as e:
            self.log.warning("输入监听启动失败(不影响画布): %s", e)

    def _stop_listeners(self):
        for l in self._listeners:
            try:
                l.stop()
            except Exception:
                pass
        self._listeners = []

    # ---------- 画笔大小 ----------
    def change_brush(self, delta: int):
        b = self.cfg.get("brush", {})
        new = self.brush_size + delta
        new = max(int(b.get("min", 1)), min(int(b.get("max", 200)), new))
        if new != self.brush_size:
            self.brush_size = new
            self.log.info("画笔大小 -> %d", new)
            # 悬浮指示
            try:
                from ui.overlay import BrushOverlay
                if self.overlay is None and self._root is not None:
                    self.overlay = BrushOverlay(self._root)
                if self.overlay is not None:
                    x, y = mouse.position()
                    self.overlay.show(new, x, y)
            except Exception:
                pass

    def toggle_channel(self):
        self.pen_channel = not self.pen_channel
        if self.injector.is_active:
            self.injector.cancel()
        ch = "笔输入(压力)" if self.pen_channel else "鼠标输入"
        self.log.info("输入通道切换: %s", ch)

    # ---------- 画布 ----------
    def open_canvas(self):
        if self.canvas is not None and self.canvas.winfo_exists():
            self.canvas.lift()
            return self.canvas
        if self._root is None:
            self.log.error("无 tk root, 无法打开画布")
            return None
        from ui.canvas import DrawCanvas
        self.canvas = DrawCanvas(
            self._root,
            pressure_mapper=self.mapper,
            on_replay=self._replay_external,
            get_brush=lambda: self.brush_size,
        )
        return self.canvas

    # ---------- 重放注入 ----------
    def _replay_external(self, screen_points, pressures):
        """把画布曲线按真实速度+压力注入外部软件"""
        if len(screen_points) < 2:
            return
        interval = float(self.cfg.get("replay_interval_ms", 8))
        speed = float(self.cfg.get("replay_speed", 1.0))
        interval = max(1.0, interval / speed)

        if self.pen_channel:
            # 笔通道: WM_POINTER 压力注入
            self.injector.pen_down(*screen_points[0], pressure=pressures[0])
            try:
                for i, pt in enumerate(screen_points[1:], start=1):
                    self.injector.pen_move(*pt, pressure=pressures[i])
                    time.sleep(interval / 1000.0)
            finally:
                self.injector.pen_up(*screen_points[-1])
            self.log.info("笔通道重放完成 %d 点", len(screen_points))
        else:
            # 鼠标通道: 移动+按住拖动(压力仅画布内有效)
            mouse.move_absolute(*screen_points[0])
            mouse.mc.press(mouse.Button.left)
            try:
                for pt in screen_points[1:]:
                    mouse.move_absolute(*pt)
                    time.sleep(interval / 1000.0)
            finally:
                mouse.mc.release(mouse.Button.left)
            self.log.info("鼠标通道重放完成 %d 点", len(screen_points))
