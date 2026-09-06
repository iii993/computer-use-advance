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


def click(button: str = "left", x: float | None = None, y: float | None = None) -> None:
    if x is not None and y is not None:
        mc.position = (x, y)
    mc.click(_btn(button), 1)


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
