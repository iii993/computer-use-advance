"""触笔压力注入引擎.

原理: 向鼠标所在窗口发送 WM_POINTERDOWN/UPDATE/UP 消息, 模拟触屏笔输入.
压力通过 POINTER_PEN_INFO.pressure(0-1024) 传递, 目标软件调用
GetPointerPenInfo 时读取.

适用范围:
- 演示画布(ui/canvas.py): 完整压力生效
- 外部 Win32 绘图软件(画图/PS 等): 坐标/按下/抬起生效; 压力是否生效
  取决于目标软件是否支持指针查询, 部分软件自带"鼠标模拟压力"选项可配合
"""
import ctypes
from ctypes import wintypes

# ---- 常量 ----
WM_POINTERUPDATE = 0x0245
WM_POINTERDOWN = 0x0246
WM_POINTERUP = 0x0247
PT_PEN = 0x00000002

POINTER_FLAG_NONE = 0x00000000
POINTER_FLAG_NEW = 0x00000001
POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_FIRSTBUTTON = 0x00000010
POINTER_FLAG_SECONDBUTTON = 0x00000020
POINTER_FLAG_PRIMARY = 0x00000040
POINTER_FLAG_CONFIDENCE = 0x00000080
POINTER_FLAG_CANCELED = 0x00000100
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UPDATE = 0x00020000
POINTER_FLAG_UP = 0x00040000

# ---- Win32 结构 ----
class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

class POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerType", wintypes.DWORD),
        ("pointerId", wintypes.UINT),
        ("frameId", wintypes.UINT),
        ("pointerFlags", wintypes.DWORD),
        ("sourceDevice", ctypes.c_void_p),
        ("hwndTarget", wintypes.HWND),
        ("ptPixelLocation", POINT),
        ("ptHimetricLocation", POINT),
        ("ptPixelLocationRaw", POINT),
        ("ptHimetricLocationRaw", POINT),
        ("dwTime", wintypes.DWORD),
        ("historyCount", wintypes.ULONG),
        ("InputData", wintypes.LONG),
        ("dwKeyStates", wintypes.DWORD),
        ("PerformanceCount", ctypes.c_ulonglong),
        ("ButtonChangeType", wintypes.DWORD),
    ]

class POINTER_PEN_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", POINTER_INFO),
        ("penFlags", wintypes.DWORD),
        ("penMask", wintypes.DWORD),
        ("pressure", wintypes.UINT),
        ("rotation", wintypes.UINT),
        ("tiltX", wintypes.LONG),
        ("tiltY", wintypes.LONG),
    ]

_user32 = ctypes.windll.user32
_user32.WindowFromPoint.argtypes = [POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL


class PenInjector:
    """向指定窗口注入带压力的触笔事件"""

    def __init__(self, pointer_id: int = 0x7654):
        self.pointer_id = pointer_id & 0xFFFF
        self.active = False
        self.target_hwnd = None

    @property
    def is_active(self) -> bool:
        return self.active

    def _find_target(self, x: float, y: float):
        pt = POINT(int(x), int(y))
        return _user32.WindowFromPoint(pt)

    def _post(self, msg: int, x: float, y: float, flags: int) -> bool:
        wparam = ((flags & 0xFFFF) << 16) | self.pointer_id
        lparam = ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)
        return bool(_user32.PostMessageW(self.target_hwnd, msg, wparam, lparam))

    def pen_down(self, x: float, y: float, pressure: int = 1024) -> None:
        """笔按下(落笔)"""
        self.target_hwnd = self._find_target(x, y)
        flags = (POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT
                 | POINTER_FLAG_FIRSTBUTTON | POINTER_FLAG_CONFIDENCE | POINTER_FLAG_PRIMARY)
        self._post(WM_POINTERDOWN, x, y, flags)
        self.active = True

    def pen_move(self, x: float, y: float, pressure: int = 512) -> None:
        """笔移动(压感更新)"""
        if not self.active:
            return
        flags = (POINTER_FLAG_UPDATE | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT
                 | POINTER_FLAG_FIRSTBUTTON | POINTER_FLAG_CONFIDENCE)
        self._post(WM_POINTERUPDATE, x, y, flags)

    def pen_up(self, x: float, y: float) -> None:
        """笔抬起"""
        if not self.active:
            return
        flags = POINTER_FLAG_UP | POINTER_FLAG_CONFIDENCE
        self._post(WM_POINTERUP, x, y, flags)
        self.active = False

    def cancel(self) -> None:
        """强制取消(模式切换等场景)"""
        if self.active:
            self._post(WM_POINTERUP, 0, 0, POINTER_FLAG_UP | POINTER_FLAG_CANCELED)
            self.active = False


class PressureMapper:
    """速度->压力映射: 慢=重压, 快=轻扫(移动平均防抖)"""

    def __init__(self, cfg: dict):
        self.min_p = int(cfg.get("pressure_min", 50))
        self.max_p = int(cfg.get("pressure_max", 1024))
        self.slow = float(cfg.get("slow_speed", 50))
        self.fast = float(cfg.get("fast_speed", 500))
        self.window = max(1, int(cfg.get("smooth_window", 4)))
        self._history: list[float] = []
        self._last_pt = None
        self._last_time = None

    def _speed(self, pt: tuple, now: float) -> float:
        if self._last_pt is None or self._last_time is None:
            return 0.0
        dx = pt[0] - self._last_pt[0]
        dy = pt[1] - self._last_pt[1]
        dt = max(1e-6, (now - self._last_time) * 1000.0)  # ms
        return (dx * dx + dy * dy) ** 0.5 / dt * 1000.0  # px/s

    def update(self, pt: tuple, now: float | None = None) -> int:
        """根据新位置计算压力, 返回 0-1024 的整数"""
        import time as _t
        now = now if now is not None else _t.time()
        speed = self._speed(pt, now)
        self._last_pt = pt
        self._last_time = now

        norm = (speed - self.slow) / max(1e-6, (self.fast - self.slow))
        norm = max(0.0, min(1.0, norm))
        pressure = self.max_p - norm * (self.max_p - self.min_p)

        self._history.append(pressure)
        if len(self._history) > self.window:
            self._history.pop(0)
        return int(round(sum(self._history) / len(self._history)))

    def reset(self) -> None:
        self._history.clear()
        self._last_pt = None
        self._last_time = None
