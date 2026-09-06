"""键盘引擎(pynput): 按键/组合/连点宏"""
import random
import time

from pynput.keyboard import Controller as KeyboardController, Key

kc = KeyboardController()

# 特殊键名 -> pynput Key 枚举
_SPECIAL_KEYS = {
    "ctrl": Key.ctrl, "control": Key.ctrl, "lctrl": Key.ctrl, "rctrl": Key.ctrl,
    "shift": Key.shift, "lshift": Key.shift, "rshift": Key.shift,
    "alt": Key.alt, "lalt": Key.alt, "ralt": Key.alt, "alt_gr": Key.alt_gr,
    "space": Key.space, "enter": Key.enter, "return": Key.enter,
    "tab": Key.tab, "esc": Key.esc, "escape": Key.esc,
    "backspace": Key.backspace, "delete": Key.delete, "insert": Key.insert,
    "home": Key.home, "end": Key.end,
    "page_up": Key.page_up, "page_down": Key.page_down,
    "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
    "cmd": Key.cmd, "win": Key.cmd, "super": Key.cmd,
    "caps_lock": Key.caps_lock, "num_lock": Key.num_lock, "scroll_lock": Key.scroll_lock,
    "print_screen": Key.print_screen, "pause": Key.pause, "menu": Key.menu,
}


def _to_key(key: str):
    """按键名 -> pynput key 对象(特殊键枚举或字符)"""
    k = str(key).lower()
    if k in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[k]
    if k.startswith("f") and k[1:].isdigit():
        n = int(k[1:])
        return getattr(Key, f"f{n}", None) or k
    if len(k) == 1:
        return k
    return k


def tap(key: str, interval_ms: float = 40) -> None:
    """单击按键"""
    k = _to_key(key)
    kc.press(k)
    time.sleep(interval_ms / 1000.0)
    kc.release(k)


def press(key: str) -> None:
    kc.press(_to_key(key))


def release(key: str) -> None:
    kc.release(_to_key(key))


def combo(keys: list) -> None:
    """组合键: 依次按下, 逆序释放 (如 ['ctrl','c'])"""
    objs = [_to_key(k) for k in keys]
    for k in objs:
        kc.press(k)
    time.sleep(0.02)
    for k in reversed(objs):
        kc.release(k)


def tap_repeat(key: str, times: int, interval_ms: float = 180, jitter_ms: float = 30) -> None:
    """连点: 次数/间隔/抖动"""
    for _ in range(times):
        tap(key, 30)
        delay = interval_ms + random.uniform(-jitter_ms, jitter_ms)
        time.sleep(max(5, delay) / 1000.0)


def type_chars(text: str, delay_min_ms: float = 40, delay_max_ms: float = 120,
               word_pause_ms: float = 150) -> None:
    """逐字符输入(仅 ASCII), 随机延迟 + 词间停顿"""
    for ch in text:
        if ch == "\n":
            kc.press(Key.enter)
            kc.release(Key.enter)
        else:
            kc.type(ch)
        d = random.uniform(delay_min_ms, delay_max_ms)
        if ch == " ":
            d += word_pause_ms
        time.sleep(d / 1000.0)
