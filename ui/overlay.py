"""悬浮指示器: 无边框小窗跟随鼠标显示画笔大小, 3秒无操作自动隐藏"""
import tkinter as tk


class BrushOverlay:
    def __init__(self, root):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)   # 无边框
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.9)
        self.label = tk.Label(self.win, text="画笔: 6px", bg="#1e293b", fg="white",
                              font=("Microsoft YaHei", 11, "bold"),
                              padx=10, pady=4)
        self.label.pack()
        self.visible = False
        self._hide_after = None
        self.win.withdraw()

    def show(self, brush_size: int, x: int, y: int):
        self.label.config(text=f"画笔: {brush_size}px")
        if not self.visible:
            self.win.deiconify()
            self.visible = True
        # 跟随鼠标(偏移避免遮挡光标)
        self.win.geometry(f"+{x + 18}+{y + 18}")
        # 重置隐藏定时器
        if self._hide_after:
            self.win.after_cancel(self._hide_after)
        self._hide_after = self.win.after(3000, self.hide)

    def hide(self):
        if self.visible:
            self.win.withdraw()
            self.visible = False

    def destroy(self):
        if self._hide_after:
            try:
                self.win.after_cancel(self._hide_after)
            except Exception:
                pass
        self.win.destroy()
