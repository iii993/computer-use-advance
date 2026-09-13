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
import time
import uuid
from ctypes import (POINTER, byref, c_void_p, c_ulong, c_ushort, c_ubyte,
                    c_int, c_long, c_bool, HRESULT, wintypes)

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
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = [c_void_p, c_void_p]
_user32.GetGUIThreadInfo.restype = wintypes.BOOL
_user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, c_void_p]


class _GUITHREADINFO(ctypes.Structure):
    """GetGUIThreadInfo 的输出: 前台线程真正持有键盘焦点的窗口。

    GetForegroundWindow 只说明"谁在最前面", hwndFocus 才是**键盘事件实际去处**;
    两者可能不一致(输入法、切换过程中、无激活窗口时)。
    """
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", c_void_p), ("hwndFocus", c_void_p),
                ("hwndCapture", c_void_p), ("hwndMenuOwner", c_void_p),
                ("hwndMoveSize", c_void_p), ("hwndCaret", c_void_p),
                ("rcCaret", wintypes.LONG * 4)]

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

    def format_windows(self, max_items: int = 30, windows=None, title=None) -> str:
        """把窗口清单渲染成紧凑文本, 直接给模型读。

        windows 给了就用它(调用方可能已经过滤/排序), 否则现取 list_windows();
        title 给了则只保留标题匹配的窗口。
        """
        items = list(windows) if windows is not None else self.list_windows()
        if title:
            items = [w for w in items if self._title_score(title, w) > 0]
        lines = []
        for i, w in enumerate(items[:max_items], 1):
            name = w.get("name") or w.get("win32_title") or ""
            lines.append(
                f"[{i}] hwnd={w['hwnd']} type={w.get('control_type_name','?')} "
                f"rect={w.get('rect')} class={w.get('class','')!r} name={name!r}")
        return "\n".join(lines) if lines else "(未发现可见顶层窗口)"

    # ---- 窗口查找与激活 ----
    @staticmethod
    def _title_score(title: str, w: dict) -> int:
        """标题匹配打分(越大越精确)。

        真机教训: 早先只用"子串命中", 于是 title="Krita" 会先撞上 Everything 的
        "krita.e - Everything" 窗口。现在按 完全相等 > 词边界 > 前缀 > 子串 分级,
        且 name 的权重高于 win32_title, class 只作为最后兜底。
        """

        def score_one(text: str, base: int) -> int:
            text = (text or "").strip().lower()
            if not text:
                return 0
            if text == t:
                return base
            for sep in (" - ", " – ", " — ", " | ", ": ", " · "):
                if text.startswith(t + sep) or text.endswith(sep + t):
                    return base - 15
            if text.startswith(t):
                return base - 30
            if t in text:
                return base - 45
            return 0

        t = str(title or "").strip().lower()
        if not t:
            return 0
        cls = str(w.get("class") or "").lower()
        return max(score_one(w.get("name"), 100),
                   score_one(w.get("win32_title"), 95),
                   25 if t in cls else 0)

    def find_window(self, hwnd=None, title=None, with_candidates: bool = False):
        """按 hwnd 或标题找一个可见顶层窗口。

        - hwnd 给了就精确匹配, 找到即返回
        - title 走 _title_score 排序: 分数高者优先, 同分时优先"有实际面积的窗口"、
          再优先标题更短的(避免长标题里恰好含关键词的窗口胜出)
        - with_candidates=True 时返回 (best, others);others 是次优候选(最多 5 个),
          用来告诉调用方"这里其实有歧义, 建议改用 hwnd"
        """
        windows = self.list_windows()
        if hwnd is not None:
            for w in windows:
                if int(w.get("hwnd") or 0) == int(hwnd):
                    return (w, []) if with_candidates else w
            return (None, []) if with_candidates else None
        if not title:
            return (None, []) if with_candidates else None

        ranked = []
        for w in windows:
            s = self._title_score(title, w)
            if s <= 0:
                continue
            rect = w.get("rect") or (0, 0, 0, 0)
            try:
                area = max(0, int(rect[2]) - int(rect[0])) * max(0, int(rect[3]) - int(rect[1]))
            except (TypeError, ValueError, IndexError):
                area = 0
            label = str(w.get("name") or w.get("win32_title") or "")
            ranked.append((-s, 0 if area > 0 else 1, len(label), w))

        if not ranked:
            return (None, []) if with_candidates else None
        ranked.sort(key=lambda item: item[:3])
        best = ranked[0][3]
        if not with_candidates:
            return best
        others = [{"hwnd": item[3].get("hwnd"),
                   "name": item[3].get("name") or item[3].get("win32_title"),
                   "class": item[3].get("class")}
                  for item in ranked[1:6]]
        return best, others

    def foreground_info(self) -> dict:
        """当前前台窗口 + 真实键盘焦点窗口(GetGUIThreadInfo)。

        GetForegroundWindow 只说"谁在最前面", hwndFocus 才是键盘事件的实际去处。
        """
        fg = int(_user32.GetForegroundWindow() or 0)
        info = {"foreground": fg, "active": 0, "focus": 0}
        if not fg:
            return info
        try:
            tid = _user32.GetWindowThreadProcessId(c_void_p(fg), None)
            g = _GUITHREADINFO()
            g.cbSize = ctypes.sizeof(_GUITHREADINFO)
            if _user32.GetGUIThreadInfo(tid, ctypes.byref(g)):
                info["active"] = int(g.hwndActive or 0)
                info["focus"] = int(g.hwndFocus or 0)
        except OSError:
            pass
        return info

    def focus(self, hwnd, wait_seconds: float = 2.0, poll_interval: float = 0.05) -> dict:
        """激活窗口到前台, 并**确认它真的成了前台**才返回成功。

        真机实测(2026-09-13): SetForegroundWindow 是**异步**的 —— 它返回非零时前台
        可能还没切换完(GetForegroundWindow 此刻甚至返回 0)。旧实现直接信它的返回值,
        于是 ok:true 之后紧接着注入的按键会打到**旧窗口**上(用户看到按键全进了浏览器)。
        现在改成轮询确认, 确认失败就如实返回 ok:false + 实际前台窗口。

        返回 {ok, already, foreground, active, focus}。
        """
        want = int(hwnd)
        h = c_void_p(want)
        try:
            if _user32.IsIconic(h):
                _user32.ShowWindow(h, SW_RESTORE)
            if int(_user32.GetForegroundWindow() or 0) == want:
                return {"ok": True, "already": True, **self.foreground_info()}
            _user32.SetForegroundWindow(h)
            deadline = time.monotonic() + max(0.0, float(wait_seconds))
            while True:
                info = self.foreground_info()
                if info["foreground"] == want:
                    return {"ok": True, "already": False, **info}
                if time.monotonic() >= deadline:
                    return {"ok": False, "already": False, **info}
                time.sleep(max(0.0, poll_interval))
        except OSError:
            return {"ok": False, "already": False, "foreground": 0, "active": 0, "focus": 0}
