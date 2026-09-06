"""几何工具: RDP 曲线简化 + 点到线段距离"""
from typing import List, Tuple

Point = Tuple[float, float]


def _point_line_dist(p: Point, a: Point, b: Point) -> float:
    """点到线段 ab 的距离(投影判断)"""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    proj_x, proj_y = ax + t * dx, ay + t * dy
    return ((px - proj_x) ** 2 + (py - proj_y) ** 2) ** 0.5


def rdp_simplify(points: List[Point], epsilon: float = 4.0) -> List[Point]:
    """Ramer-Douglas-Peucker 曲线简化: 用更少的控制点近似原轨迹.

    points: 原始采样点(>=2)
    epsilon: 容差(像素), 越大控制点越少
    返回: 控制点序列(包含首尾)
    """
    if len(points) < 3:
        return list(points)
    start, end = points[0], points[-1]
    dmax, index = 0.0, 0
    for i in range(1, len(points) - 1):
        d = _point_line_dist(points[i], start, end)
        if d > dmax:
            dmax, index = d, i
    if dmax > epsilon:
        left = rdp_simplify(points[: index + 1], epsilon)
        right = rdp_simplify(points[index:], epsilon)
        return left[:-1] + right
    return [start, end]


def nearest_segment(p: Point, points: List[Point]) -> Tuple[int, Point]:
    """返回点 p 距离控制点序列最近线段的下标与投影点(用于插入新控制点)"""
    best_i, best_dist, best_proj = 0, float("inf"), p
    for i in range(len(points) - 1):
        d = _point_line_dist(p, points[i], points[i + 1])
        if d < best_dist:
            best_dist, best_i = d, i
            a, b = points[i], points[i + 1]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length_sq = dx * dx + dy * dy
            t = 0.5 if length_sq == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
            best_proj = (a[0] + t * dx, a[1] + t * dy)
    return best_i, best_proj
