"""输入法(IME)控制: 纯 ctypes, 零第三方依赖。

背景(2026-09-13 真机):
玩游戏 / 连发按键时, **中文输入法会拦截注入的字符键** —— 按键要么被 IME 吃掉不
进游戏, 要么只上屏到别的窗口; 更糟的是 IME 可能吞掉 KeyUp 造成"卡键"(角色一直
往一个方向走)。所以游戏 / 连发 / 需要连续按键的场景要主动切到英文布局。

实测要点:
- 注入按键(pynput → SendInput)的归属由**前台窗口的输入法上下文**决定
- 切换输入法的标准做法: 向目标窗口 PostMessage(WM_INPUTLANGCHANGEREQUEST)
- GetKeyboardLayout(tid) 才能拿到"目标窗口所在线程"的布局; 传 0 只拿本线程的
"""
import ctypes
from ctypes import c_void_p, c_uint, wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.GetKeyboardLayout.restype = c_void_p
_user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
_user32.GetKeyboardLayoutNameW.restype = wintypes.BOOL
_user32.GetKeyboardLayoutNameW.argtypes = [wintypes.LPWSTR]
_user32.LoadKeyboardLayoutW.restype = c_void_p
_user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, c_uint]
_user32.ActivateKeyboardLayout.restype = c_void_p
_user32.ActivateKeyboardLayout.argtypes = [c_void_p, c_uint]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.PostMessageW.argtypes = [c_void_p, c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = [c_void_p, c_void_p]

WM_INPUTLANGCHANGEREQUEST = 0x0050
KLF_ACTIVATE = 0x0001
KLF_SETFORPROCESS = 0x0100

# 布局名 -> (KLID, 语言 ID, 说明)。KLID 是 LoadKeyboardLayout 要的 8 位十六进制串。
LAYOUTS = {
    "en": ("00000409", 0x0409, "English (US)"),
    "zh": ("00000804", 0x0804, "中文(简体) 微软拼音"),
}

# 语言 ID -> 说明(用于回报"当前是哪种输入法")
LANG_NAMES = {
    0x0409: "English (US)",
    0x0809: "English (UK)",
    0x0804: "中文(简体)",
    0x0404: "中文(繁体)",
    0x0411: "日本語",
    0x0412: "한국어",
}


def describe_layout(hkl) -> dict:
    """把 HKL 解析成可读信息(纯函数, 用于测试与回报)。

    HKL 低 16 位是语言 ID。中文输入法(微软拼音)是 0x0804。
    """
    value = int(hkl or 0)
    lang = value & 0xFFFF
    name = LANG_NAMES.get(lang)
    return {
        "hkl": value,
        "hkl_hex": "0x%08X" % (value & 0xFFFFFFFF),
        "lang_id": "0x%04X" % lang,
        "name": name or ("未知布局(0x%04X)" % lang),
        "is_chinese": lang in (0x0804, 0x0404),
        "is_english": lang in (0x0409, 0x0809),
    }


def current_layout(hwnd=None) -> dict:
    """当前输入法。给了 hwnd 就查**那个窗口所在线程**的布局(才是有意义的那个)。"""
    if hwnd:
        tid = _user32.GetWindowThreadProcessId(c_void_p(int(hwnd)), None)
    else:
        tid = _kernel32.GetCurrentThreadId()
    hkl = _user32.GetKeyboardLayout(tid) or 0
    info = describe_layout(hkl)
    buf = ctypes.create_unicode_buffer(9)
    if _user32.GetKeyboardLayoutNameW(buf):
        info["klid"] = buf.value
    info["thread_id"] = int(tid or 0)
    return info


def switch_to(layout="en", hwnd=None) -> dict:
    """切换输入法。

    - layout: "en" / "zh"(见 LAYOUTS), 或直接给 HKL 整数
    - hwnd:   目标窗口。给了就向它 PostMessage(WM_INPUTLANGCHANGEREQUEST),
              这是最贴合"让某个窗口用英文输入法"的做法; 不给则切当前进程。
    返回 {ok, before, after, target_hwnd, method}。
    """
    before = current_layout(hwnd)
    if isinstance(layout, int):
        hkl = layout
        label = "HKL %d" % layout
    else:
        entry = LAYOUTS.get(str(layout).lower())
        if entry is None:
            raise ValueError("未知 layout=%r, 可用: %s" % (layout, sorted(LAYOUTS)))
        hkl = _user32.LoadKeyboardLayoutW(entry[0], KLF_ACTIVATE) or 0
        label = entry[2]
        if not hkl:
            # 极端情况下系统没装该布局: 回退到"按语言 ID 拼一个 HKL"是不安全的, 直接报错
            raise RuntimeError("LoadKeyboardLayout 失败: %s" % entry[0])
    if hwnd:
        _user32.PostMessageW(c_void_p(int(hwnd)), WM_INPUTLANGCHANGEREQUEST, 0, int(hkl))
        method = "PostMessage(WM_INPUTLANGCHANGEREQUEST)"
    else:
        _user32.ActivateKeyboardLayout(c_void_p(int(hkl)), KLF_SETFORPROCESS)
        method = "ActivateKeyboardLayout(KLF_SETFORPROCESS)"
    return {"ok": True, "layout": label, "target_hwnd": int(hwnd) if hwnd else None,
            "before": before, "requested": describe_layout(hkl), "method": method}


def ensure_english(hwnd=None) -> dict:
    """确保用英文布局(已经是英文就原样返回, 不做多余切换)。"""
    now = current_layout(hwnd)
    if now.get("is_english"):
        return {"ok": True, "changed": False, "current": now}
    res = switch_to("en", hwnd=hwnd)
    res["changed"] = True
    res["current"] = now
    return res
