"""UIA 文本观察通道(纯 ctypes, 零第三方依赖)。

实测环境: Python 3.14.3 / Windows / 1920x1080, 2026-09-11。

已验证:
  - CoInitialize + CoCreateInstance(CLSID_CUIAutomation)
  - IUIAutomation         : GetRootElement / ElementFromHandle
  - IUIAutomationElement  : ControlType / Name / AutomationId / ClassName / BoundingRectangle
  - EnumWindows + ElementFromHandle 窗口级观察

未打通(见计划文档 §5):
  - 控件级遍历(FindAll 返回 E_FAIL; TreeWalker 索引未定位)

注意:
  - COM 为 STA, UIA 实例必须在创建它的线程内使用
  - 读 BSTR 用 c_void_p + wstring_at; 未调用 SysFreeString, 存在轻微 BSTR 泄漏(已知)
  - vtable 索引是实测结论, 改动前必须重新验证(盲扫会 access violation)
"""
import ctypes
import uuid
from ctypes import (POINTER, byref, c_void_p, c_ulong, c_ushort, c_ubyte,
                    c_int, c_long, c_bool, HRESULT)

CLSID_CUIAutomation = "{FF48DBA4-60EF-4201-AA87-54103EEF594E}"
IID_IUIAutomation   = "{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}"

CLSCTX_INPROC_SERVER = 1

# UIA_ControlTypeIds 常用子集(50032=Window / 50033=Pane 已实测)
CONTROL_TYPES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
    50039: "SemanticZoom", 50040: "AppBar",
}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort),
                ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


class RECT(ctypes.Structure):
    _fields_ = [("left", c_long), ("top", c_long),
                ("right", c_long), ("bottom", c_long)]


def _guid(s: str) -> _GUID:
    u = uuid.UUID(s)
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


_ole32 = ctypes.WinDLL("ole32")
_ole32.CoInitialize.restype = HRESULT
_ole32.CoInitialize.argtypes = [c_void_p]
_ole32.CoCreateInstance.restype = HRESULT
_ole32.CoCreateInstance.argtypes = [POINTER(_GUID), c_void_p, c_ulong,
                                    POINTER(_GUID), POINTER(c_void_p)]

_user32 = ctypes.WinDLL("user32")
_EnumWindowsProc = ctypes.WINFUNCTYPE(c_bool, c_void_p, c_void_p)
_user32.EnumWindows.restype = c_bool
_user32.EnumWindows.argtypes = [_EnumWindowsProc, c_void_p]
_user32.IsWindowVisible.restype = c_bool
_user32.IsWindowVisible.argtypes = [c_void_p]
_user32.GetWindowTextLengthW.restype = c_int
_user32.GetWindowTextLengthW.argtypes = [c_void_p]
_user32.GetWindowTextW.restype = c_int
_user32.GetWindowTextW.argtypes = [c_void_p, ctypes.c_wchar_p, c_int]
_user32.IsIconic.restype = c_bool
_user32.IsIconic.argtypes = [c_void_p]
_user32.ShowWindow.restype = c_bool
_user32.ShowWindow.argtypes = [c_void_p, c_int]
_user32.SetForegroundWindow.restype = c_bool
_user32.SetForegroundWindow.argtypes = [c_void_p]
_user32.GetForegroundWindow.restype = c_void_p
_user32.GetForegroundWindow.argtypes = []

SW_RESTORE = 9          # ShowWindow: 还原并激活最小化窗口


def _vtable(obj) -> POINTER(c_void_p):
    return ctypes.cast(ctypes.cast(obj, POINTER(c_void_p))[0], POINTER(c_void_p))


def _fn(vt, idx, *argtypes):
    """绑定 vtable 第 idx 个方法。

    注意: restype=HRESULT 时 ctypes 会自动抛 OSError, 调用处必须 try/except 才能看到 hr。
    """
    return ctypes.WINFUNCTYPE(HRESULT, c_void_p, *argtypes)(vt[idx])


class UIA:
    # ---- 已验证的 vtable 索引(勿改, 改前重测) ----
    _IDX_GET_ROOT      = 5
    _IDX_FROM_HWND     = 6
    _IDX_CONTROL_TYPE  = 21
    _IDX_NAME          = 23
    _IDX_AUTOMATION_ID = 29
    _IDX_CLASS         = 30
    _IDX_RECT          = 43

    def __init__(self):
        hr = _ole32.CoInitialize(None)
        self._need_uninit = hr in (0, 1)
        self._p = c_void_p()
        hr = _ole32.CoCreateInstance(
            byref(_guid(CLSID_CUIAutomation)), None, CLSCTX_INPROC_SERVER,
            byref(_guid(IID_IUIAutomation)), byref(self._p))
        if hr != 0 or not self._p.value:
            raise RuntimeError(f"创建 CUIAutomation 失败, hr={hr}")
        vt = _vtable(self._p)
        self._from_hwnd = _fn(vt, self._IDX_FROM_HWND, c_void_p, POINTER(c_void_p))

    # ---- 属性读取 ----
    def _str(self, elem, idx) -> str:
        v = c_void_p()
        try:
            _fn(_vtable(elem), idx, POINTER(c_void_p))(elem, byref(v))
        except OSError:
            return ""
        if not v.value:
            return ""
        # 注: 未调用 SysFreeString, 存在轻微 BSTR 泄漏(待优化)
        return ctypes.wstring_at(v.value)

    def _int(self, elem, idx) -> int:
        v = c_int()
        try:
            _fn(_vtable(elem), idx, POINTER(c_int))(elem, byref(v))
        except OSError:
            return 0
        return v.value

    def _rect(self, elem) -> tuple:
        r = RECT()
        try:
            _fn(_vtable(elem), self._IDX_RECT, POINTER(RECT))(elem, byref(r))
        except OSError:
            return (0, 0, 0, 0)
        return (r.left, r.top, r.right, r.bottom)

    def element_from_hwnd(self, hwnd):
        e = c_void_p()
        try:
            hr = self._from_hwnd(self._p, c_void_p(hwnd), byref(e))
        except OSError:
            return None
        return e if (hr == 0 and e.value) else None

    def describe(self, elem) -> dict:
        ct = self._int(elem, self._IDX_CONTROL_TYPE)
        return {
            "name": self._str(elem, self._IDX_NAME),
            "class": self._str(elem, self._IDX_CLASS),
            "automation_id": self._str(elem, self._IDX_AUTOMATION_ID),
            "control_type": ct,
            "control_type_name": CONTROL_TYPES.get(ct, "Unknown"),
            "rect": self._rect(elem),
        }

    # ---- 窗口级观察 ----
    def list_windows(self, visible_only=True, with_title_only=True) -> list:
        """枚举顶层窗口并读 UIA 属性。返回 list[dict]。"""
        found = []

        def _cb(hwnd, _lparam):
            if visible_only and not _user32.IsWindowVisible(hwnd):
                return True
            n = _user32.GetWindowTextLengthW(hwnd)
            if with_title_only and n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            _user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value

            rec = {"hwnd": hwnd, "win32_title": title}
            el = self.element_from_hwnd(hwnd)
            if el is not None:
                rec.update(self.describe(el))
            found.append(rec)
            return True

        _user32.EnumWindows(_EnumWindowsProc(_cb), None)
        return found

    def format_windows(self, max_items: int = 30) -> str:
        """把窗口清单渲染成紧凑文本, 直接给模型读。"""
        lines = []
        for i, w in enumerate(self.list_windows()[:max_items], 1):
            name = w.get("name") or w.get("win32_title") or ""
            lines.append(
                f"[{i}] hwnd={w['hwnd']} type={w.get('control_type_name','?')} "
                f"rect={w.get('rect')} class={w.get('class','')!r} name={name!r}")
        return "\n".join(lines) if lines else "(未发现可见顶层窗口)"

    # ---- 窗口查找与激活 ----
    def find_window(self, hwnd=None, title=None) -> dict | None:
        """按 hwnd 或标题子串(不区分大小写, 匹配 name/win32_title/class)找一个可见顶层窗口。"""
        if hwnd is None and not title:
            return None
        for w in self.list_windows():
            if hwnd is not None and int(w.get("hwnd") or 0) == int(hwnd):
                return w
            if title:
                hay = " ".join(str(w.get(k, "")) for k in ("name", "win32_title", "class")).lower()
                if str(title).lower() in hay:
                    return w
        return None

    def focus(self, hwnd) -> bool:
        """把窗口激活到前台(最小化时先还原)。返回"调用后它是否在前台"。

        真机实测: 窗口已经在前台时 SetForegroundWindow 常常返回 0, 那不是失败, 所以先看
        GetForegroundWindow。目标窗口若以管理员权限运行, 普通权限进程确实无法前置。
        """
        h = c_void_p(int(hwnd))
        try:
            if _user32.IsIconic(h):
                _user32.ShowWindow(h, SW_RESTORE)
            if int(_user32.GetForegroundWindow() or 0) == int(hwnd):
                return True
            return bool(_user32.SetForegroundWindow(h))
        except OSError:
            return False
