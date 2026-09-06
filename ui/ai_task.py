"""AI 任务窗口: 输入任务 -> 后台运行 AI 循环 -> 显示步骤与结果"""
import threading
import tkinter as tk
from tkinter import scrolledtext


class AITaskWindow(tk.Toplevel):
    def __init__(self, master, controller, on_close=None):
        super().__init__(master)
        self.controller = controller
        self.on_close = on_close
        self.running = False
        self._stop = threading.Event()

        self.title("🤖 AI 电脑操控任务")
        self.geometry("640x520+200+100")
        self.configure(bg="#f5f5f5")

        tk.Label(self, text="任务描述(例如: 打开计算器, 计算 3+5 并截图结果)",
                 bg="#f5f5f5", font=("Microsoft YaHei", 10)).pack(anchor="w", padx=12, pady=(12, 4))

        self.task_entry = tk.Text(self, height=2, font=("Microsoft YaHei", 10))
        self.task_entry.pack(fill=tk.X, padx=12)

        btns = tk.Frame(self, bg="#f5f5f5")
        btns.pack(fill=tk.X, padx=12, pady=6)
        self.run_btn = tk.Button(btns, text="▶ 开始执行", command=self.start,
                                 bg="#2563eb", fg="white", padx=16, pady=4,
                                 font=("Microsoft YaHei", 10))
        self.run_btn.pack(side=tk.LEFT)
        self.stop_btn = tk.Button(btns, text="■ 停止", command=self.stop,
                                  bg="#dc2626", fg="white", padx=16, pady=4,
                                  font=("Microsoft YaHei", 10), state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=8)
        self.status = tk.Label(btns, text="就绪", bg="#f5f5f5", fg="#666",
                               font=("Microsoft YaHei", 9))
        self.status.pack(side=tk.RIGHT)

        tk.Label(self, text="执行日志:", bg="#f5f5f5", font=("Microsoft YaHei", 9),
                 fg="#555").pack(anchor="w", padx=12)
        self.log_box = scrolledtext.ScrolledText(self, height=16, state=tk.DISABLED,
                                                 font=("Consolas", 9))
        self.log_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---------- UI ----------
    def _log(self, msg: str):
        self.log_box.config(state=tk.NORMAL)
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)
        self.log_box.config(state=tk.DISABLED)

    def _set_status(self, text: str):
        self.status.config(text=text)

    # ---------- 运行 ----------
    def start(self):
        task = self.task_entry.get("1.0", tk.END).strip()
        if not task:
            self._set_status("请输入任务描述")
            return
        if self.running:
            return
        self.running = True
        self._stop.clear()
        self.run_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self._set_status("运行中…")
        self._log(">>> 任务: " + task)

        def worker():
            def on_step(kind, step):
                self.after(0, self._log, "[步骤 " + str(step) + "] 已截图, 等待模型决策…")

            try:
                result = self.controller.run_task(task, on_step=on_step)
                self.after(0, self._finish, result)
            except Exception as e:
                self.after(0, self._finish, {"ok": False, "error": str(e)})

        threading.Thread(target=worker, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._set_status("停止中…")

    def _finish(self, result: dict):
        self.running = False
        self.run_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        if result.get("ok"):
            self._set_status("✅ 完成 (步数: " + str(result.get("steps", 0)) + ")")
            self._log("<<< 完成: " + str(result.get("result", "")))
        else:
            self._set_status("❌ 失败")
            self._log("<<< 失败: " + str(result.get("error", "未知错误")))

    def _close(self):
        self._stop.set()
        self.destroy()
        if self.on_close:
            self.on_close()
