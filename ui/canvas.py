"""演示画布: tkinter Canvas 实现曲线绘制 + 控制点编辑系统.

交互:
- 手绘: 鼠标按下拖动画曲线, 抬手后 RDP 自动简化为控制点
- 编辑: 拖动控制点调整曲线 / 点击空白处插入控制点 / 双击控制点删除
- 空格: 重放曲线(回调 on_replay, 可注入外部软件)
- 压力笔迹: 线条宽度随 PressureMapper 压力变化
"""
import math
import tkinter as tk

from input_engine.mouse import midpoint_bezier_curve
from utils.geometry import nearest_segment, rdp_simplify

CP_RADIUS = 6          # 控制点绘制半径
HIT_RADIUS = 12        # 控制点命中半径


class DrawCanvas(tk.Toplevel):
    def __init__(self, master, pressure_mapper, on_replay=None, get_brush=None):
        super().__init__(master)
        self.title("绘画演示画布 — 手绘→控制点→空格重放")
        self.geometry("900x600+300+150")
        self.configure(bg="#f5f5f5")

        self.mapper = pressure_mapper      # PressureMapper 实例
        self.on_replay = on_replay         # 回调: (screen_points, pressures)
        self.get_brush = get_brush         # 回调: () -> 画笔大小

        self.cv = tk.Canvas(self, bg="white", highlightthickness=1,
                            highlightbackground="#ccc")
        self.cv.pack(fill=tk.BOTH, expand=True)

        # 数据
        self.raw_points = []               # 当前手绘原始点
        self.control_points = []           # 编辑用控制点
        self.state = "idle"                # idle | drawing | dragging_cp
        self.drag_idx = -1
        self._last_draw_id = None

        # 事件绑定
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.cv.bind("<Double-Button-1>", self._on_double)
        self.bind("<space>", self._on_space)
        self.bind("<Escape>", lambda e: self._clear())

        self._hint = tk.Label(self, text="手绘曲线 → 松开自动生成控制点 | 拖动控制点编辑 | 点击空白插入 | 双击删除 | 空格重放",
                              bg="#f5f5f5", fg="#666", font=("Microsoft YaHei", 9))
        self._hint.pack(fill=tk.X, padx=8, pady=4)

    # ---------- 坐标 ----------
    def to_screen(self, pt):
        """画布坐标 -> 屏幕坐标(重放注入用)"""
        x = self.winfo_rootx() + self.cv.winfo_x() + pt[0]
        y = self.winfo_rooty() + self.cv.winfo_y() + pt[1]
        return (x, y)

    # ---------- 事件处理 ----------
    def _on_press(self, e):
        pt = (e.x, e.y)
        idx = self._hit_control(pt)
        if idx >= 0:
            self.state = "dragging_cp"
            self.drag_idx = idx
            self._move_control(idx, pt)
        else:
            self.state = "drawing"
            self.raw_points = [pt]
            self.control_points = []
            self._render()

    def _on_drag(self, e):
        pt = (e.x, e.y)
        if self.state == "drawing":
            self.raw_points.append(pt)
            self._render()
        elif self.state == "dragging_cp":
            self._move_control(self.drag_idx, pt)
            self._render()

    def _on_release(self, e):
        if self.state == "drawing":
            if len(self.raw_points) >= 3:
                # RDP 简化生成控制点
                eps = self._cfg_rdp()
                self.control_points = rdp_simplify(self.raw_points, eps)
                self.log_status(f"已生成 {len(self.control_points)} 个控制点 (RDP ε={eps})")
            self.raw_points = []
        self.state = "idle"
        self.drag_idx = -1
        self._render()

    def _on_double(self, e):
        idx = self._hit_control((e.x, e.y))
        if idx >= 0 and len(self.control_points) > 2:
            del self.control_points[idx]
            self.log_status(f"删除控制点, 剩余 {len(self.control_points)}")
            self._render()

    def _on_space(self, e):
        if len(self.control_points) >= 2:
            self._replay()

    # ---------- 控制点操作 ----------
    def _hit_control(self, pt):
        for i, cp in enumerate(self.control_points):
            if (cp[0] - pt[0]) ** 2 + (cp[1] - pt[1]) ** 2 <= HIT_RADIUS ** 2:
                return i
        return -1

    def _move_control(self, idx, pt):
        if 0 <= idx < len(self.control_points):
            self.control_points[idx] = pt

    def _insert_control(self, pt):
        """在最近线段中点插入控制点"""
        if len(self.control_points) >= 2:
            i, proj = nearest_segment(pt, self.control_points)
            self.control_points.insert(i + 1, proj)
            self.log_status(f"插入控制点, 共 {len(self.control_points)}")
            self._render()

    def _clear(self):
        self.raw_points = []
        self.control_points = []
        self.state = "idle"
        self._render()
        self.log_status("已清空")

    # ---------- 渲染 ----------
    def _render(self):
        self.cv.delete("all")
        brush = self.get_brush() if self.get_brush else 6

        # 手绘原始轨迹(淡灰细线)
        if len(self.raw_points) >= 2:
            for i in range(len(self.raw_points) - 1):
                a, b = self.raw_points[i], self.raw_points[i + 1]
                self.cv.create_line(a[0], a[1], b[0], b[1], fill="#cccccc", width=1)

        # 平滑曲线(压力笔迹: 宽随压力)
        if len(self.control_points) >= 2:
            curve = midpoint_bezier_curve(self.control_points, segments=8)
            for i in range(len(curve) - 1):
                a, b = curve[i], curve[i + 1]
                # 模拟压力: 用线段长度估算速度->压力
                p = self.mapper.update(b)
                w = max(1.0, brush * p / 1024.0)
                color = self._pressure_color(p)
                self.cv.create_line(a[0], a[1], b[0], b[1], fill=color, width=w,
                                    capstyle=tk.ROUND, joinstyle=tk.ROUND)

        # 控制点
        for i, cp in enumerate(self.control_points):
            x, y = cp
            self.cv.create_oval(x - CP_RADIUS, y - CP_RADIUS, x + CP_RADIUS, y + CP_RADIUS,
                                fill="#3b82f6", outline="#1d4ed8", width=2)
            if i == 0 or i == len(self.control_points) - 1:
                self.cv.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#ef4444", outline="")

    def _pressure_color(self, p):
        """压力 -> 颜色: 轻=蓝, 重=红"""
        t = p / 1024.0
        r = int(59 + (239 - 59) * t)
        g = int(130 + (68 - 130) * t)
        b = int(246 - (246 - 68) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    # ---------- 重放 ----------
    def _replay(self):
        curve = midpoint_bezier_curve(self.control_points, segments=8)
        screen_pts = [self.to_screen(p) for p in curve]
        # 压力: 重新从空状态映射(均匀间隔)
        self.mapper.reset()
        pressures = [self.mapper.update(p) for p in curve]
        if self.on_replay:
            self.log_status(f"重放曲线 {len(curve)} 点…")
            self.on_replay(screen_pts, pressures)

    # ---------- 工具 ----------
    def _cfg_rdp(self):
        try:
            from utils.config import load_config
            return float(load_config()["draw"].get("rdp_epsilon", 4))
        except Exception:
            return 4.0

    def log_status(self, msg):
        self._hint.config(text=f"✓ {msg}  |  手绘→自动控制点 | 拖动编辑 | 空格重放 | Esc清空")
