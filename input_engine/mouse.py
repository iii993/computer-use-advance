"""鼠标引擎(pynput): 直线插值(抖动/加速曲线) + 平滑曲线路径 + 点击"""
import math
import random
import time

from pynput.mouse import Button, Controller as MouseController

mc = MouseController()


def _ease_in_out(t: float) -> float:
    """加速-减速曲线权重: 0->1 平滑过渡"""
    return 0.5 - 0.5 * math.cos(math.pi * t)


def _sleep(ms: float):
    if ms > 0:
        time.sleep(ms / 1000.0)


def position() -> tuple:
    return mc.position


def move_absolute(x: float, y: float):
    mc.position = (x, y)


def move_to(x: float, y: float, cfg: dict | None = None, duration_ms: float | None = None) -> None:
    """直线插值移动到目标点, 支持高斯抖动与加速-减速曲线"""
    cfg = cfg or {}
    steps = int(cfg.get("move_steps", 24))
    interval = cfg.get("move_interval_ms", 5)
    jitter = cfg.get("jitter_px", 1.2)
    accel = cfg.get("accel_curve", True)
    if duration_ms:
        interval = max(1.0, duration_ms / steps)

    cx, cy = mc.position
    for i in range(1, steps + 1):
        t = i / steps
        w = _ease_in_out(t) if accel else t
        tx = cx + (x - cx) * w
        ty = cy + (y - cy) * w
        if jitter > 0:
            tx += random.gauss(0, jitter)
            ty += random.gauss(0, jitter)
        mc.position = (tx, ty)
        _sleep(interval)


def move_relative(dx: float, dy: float, cfg: dict | None = None, duration_ms: float | None = None) -> None:
    """相对移动(游戏视角常用)"""
    x, y = mc.position
    move_to(x + dx, y + dy, cfg, duration_ms)


# ---------- 移动与点击原语(彻底解耦) ----------

BUTTONS = {
    "left": Button.left,
    "right": Button.right,
    "middle": Button.middle,
    "x1": Button.x1,   # 侧键1(通常"后退")
    "x2": Button.x2,   # 侧键2(通常"前进")
}

HOLD_MS_MAX = 5000                 # 单次按压上限, 防模型给超大值把任务卡死
DOUBLE_CLICK_INTERVAL_MS = 80      # 同一次点击内部多次 down/up 的间隔(远小于系统 500ms 阈值)
GAP_MS_DEFAULT = 0                 # 坐标序列相邻两点之间的间歇


def normalize_points(points, default_t: float) -> list:
    """把 [[x,y], [x,y,t], ...] 规范成 [(x, y, t), ...]; 非法输入抛 ValueError。"""
    if not isinstance(points, (list, tuple)) or len(points) == 0:
        raise ValueError("points 必须是非空列表, 元素形如 [x, y] 或 [x, y, t]")
    out = []
    for i, p in enumerate(points):
        if not isinstance(p, (list, tuple)) or len(p) not in (2, 3):
            raise ValueError(f"points[{i}] 必须是 [x, y] 或 [x, y, t], 收到 {p!r}")
        try:
            px, py = float(p[0]), float(p[1])
            pt = float(p[2]) if len(p) == 3 else float(default_t)
        except (TypeError, ValueError):
            raise ValueError(f"points[{i}] 含非数字: {p!r}")
        out.append((px, py, pt))
    return out


def move(x: float | None = None, y: float | None = None, mode: str = "smooth",
         duration_ms: float | None = None, points: list | None = None,
         gap_ms: float = GAP_MS_DEFAULT, cfg: dict | None = None) -> None:
    """只移动, 不点击。mode='smooth' 插值+抖动+加速; 'instant' 瞬移。

    单点: move(100, 200, duration_ms=500)
    序列: move(points=[[100, 200], [300, 400, 300]], gap_ms=120)
          (两元点用 duration_ms, 三元点的第三个数覆盖本点耗时)
    """
    if mode not in ("smooth", "instant"):
        raise ValueError(f"未知 move mode: {mode} (可选 smooth/instant)")
    if gap_ms < 0:
        raise ValueError(f"gap_ms 必须 >= 0, 收到 {gap_ms}")

    if points is not None:
        if x is not None or y is not None:
            raise ValueError("points 与 x/y 互斥, 只能给一个")
        seq = normalize_points(points, duration_ms if duration_ms is not None else 0)
        for i, (px, py, pt) in enumerate(seq):
            if pt < 0:
                raise ValueError(f"points[{i}] 的时间必须 >= 0, 收到 {pt}")
            move(px, py, mode=mode, duration_ms=(pt or None), cfg=cfg)
            if i < len(seq) - 1 and gap_ms:
                time.sleep(gap_ms / 1000.0)
        return

    if x is None or y is None:
        raise ValueError("move 需要 x/y(单点) 或 points(序列) 其中之一")

    if mode == "instant":
        mc.position = (x, y)
        return
    move_to(x, y, cfg, duration_ms)


# ---------- 曲线路径(绘画模式) ----------

def quadratic_bezier(p0: tuple, p1: tuple, p2: tuple, t: float) -> tuple:
    """二阶贝塞尔点: B(t) = (1-t)^2*P0 + 2(1-t)t*P1 + t^2*P2"""
    u = 1 - t
    x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
    y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
    return (x, y)


def midpoint_bezier_curve(points: list, segments: int = 8) -> list:
    """中点贝塞尔插值: 相邻控制点对的中点做二阶贝塞尔, 保证曲线穿过原始控制点."""
    if len(points) < 2:
        return list(points)
    if len(points) == 2:
        p0, p2 = points
        mid = ((p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2)
        return [quadratic_bezier(p0, mid, p2, i / segments) for i in range(1, segments + 1)]

    out = []
    p0, p1 = points[0], points[1]
    m1 = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
    for i in range(1, segments + 1):
        out.append(quadratic_bezier(p0, m1, p1, i / segments))
    for k in range(1, len(points) - 1):
        pk = points[k]
        mk_prev = ((points[k - 1][0] + pk[0]) / 2, (points[k - 1][1] + pk[1]) / 2)
        mk = ((pk[0] + points[k + 1][0]) / 2, (pk[1] + points[k + 1][1]) / 2)
        for i in range(1, segments + 1):
            out.append(quadratic_bezier(mk_prev, pk, mk, i / segments))
    if len(points) >= 3:
        pn2, pn1 = points[-2], points[-1]
        mn = ((pn2[0] + pn1[0]) / 2, (pn2[1] + pn1[1]) / 2)
        for i in range(1, segments + 1):
            out.append(quadratic_bezier(mn, pn2, pn1, i / segments))
    return out


def replay_path(points: list, interval_ms: float = 8, jitter: float = 0.0,
                on_step=None) -> None:
    """沿细分点序列重放移动. on_step(pt, idx) 用于同步压力/画笔事件."""
    for i, (x, y) in enumerate(points):
        tx, ty = x, y
        if jitter > 0:
            tx += random.gauss(0, jitter)
            ty += random.gauss(0, jitter)
        mc.position = (tx, ty)
        if on_step:
            on_step((tx, ty), i)
        _sleep(interval_ms)


# ---------- 点击 ----------

def _btn(button: str) -> Button:
    b = (button or "left").lower()
    if b in ("right", "rmb"):
        return Button.right
    if b in ("middle", "mmb"):
        return Button.middle
    return Button.left


def click(button: str = "left", x: float | None = None, y: float | None = None,
          hold_ms: float = 0, clicks: int = 1, interval_ms: float | None = None,
          at: tuple | None = None, points: list | None = None,
          gap_ms: float = GAP_MS_DEFAULT, cfg: dict | None = None) -> None:
    """只点击(只有给了 x/y / at / points 才会先移动)。

    - hold_ms: 按压时长(毫秒, 0~HOLD_MS_MAX), 0 表示快速点击
    - interval_ms: 同一次点击内部多次 down/up 的间隔(双击间隔), 默认 DOUBLE_CLICK_INTERVAL_MS
    - gap_ms: points 序列相邻两点之间的间歇(**与 interval_ms 含义不同**)
    - 兼容旧签名: click("right", 100, 200) 等价于 click("right", at=(100, 200))
    单点: click(button="x1", hold_ms=300, at=(100, 200))
    序列: click(points=[[100, 200], [300, 400, 60]], gap_ms=200)
    """
    btn = BUTTONS.get((button or "left").lower())
    if btn is None:
        raise ValueError(f"未知按键: {button} (可选 {sorted(BUTTONS)})")
    if hold_ms < 0 or hold_ms > HOLD_MS_MAX:
        raise ValueError(f"hold_ms 必须在 0~{HOLD_MS_MAX} 之间, 收到 {hold_ms}")
    if clicks < 1:
        raise ValueError(f"clicks 必须 >= 1, 收到 {clicks}")
    if gap_ms < 0:
        raise ValueError(f"gap_ms 必须 >= 0, 收到 {gap_ms}")

    # 兼容旧签名: click(button, x, y)
    if x is not None or y is not None:
        if x is None or y is None:
            raise ValueError("x 与 y 必须同时提供")
        if at is not None:
            raise ValueError("x/y 与 at 互斥, 只能给一个")
        at = (x, y)

    if points is not None:
        if at is not None:
            raise ValueError("points 与 at 互斥, 只能给一个")
        seq = normalize_points(points, hold_ms)
        for i, (px, py, ph) in enumerate(seq):
            if ph < 0 or ph > HOLD_MS_MAX:
                raise ValueError(f"points[{i}] 的按压时长必须在 0~{HOLD_MS_MAX} 之间, 收到 {ph}")
            click(button=button, hold_ms=ph, clicks=clicks,
                  interval_ms=interval_ms, at=(px, py), cfg=cfg)
            if i < len(seq) - 1 and gap_ms:
                time.sleep(gap_ms / 1000.0)
        return

    if interval_ms is None:
        interval_ms = DOUBLE_CLICK_INTERVAL_MS

    if at is not None:
        mc.position = (at[0], at[1])       # 点击前定位保持旧行为(瞬移)

    for i in range(clicks):
        if hold_ms > 0:
            mc.press(btn)
            time.sleep(hold_ms / 1000.0)
            mc.release(btn)
        else:
            mc.click(btn, 1)
        if i < clicks - 1:
            time.sleep(interval_ms / 1000.0)


def double_click(x: float | None = None, y: float | None = None) -> None:
    if x is not None and y is not None:
        mc.position = (x, y)
    mc.click(Button.left, 2)


def right_click(x: float | None = None, y: float | None = None) -> None:
    click("right", x, y)


def drag(start: tuple, end: tuple, duration_ms: float = 400, cfg: dict | None = None) -> None:
    """拖拽: 按住左键从 start 移动到 end"""
    mc.position = start
    mc.press(Button.left)
    try:
        move_to(end[0], end[1], cfg, duration_ms)
    finally:
        mc.release(Button.left)
