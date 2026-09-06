"""文字输入: 英文/符号直输(自然节奏), 中文/长文本走剪贴板"""
import random
import time

from pynput.keyboard import Controller as KeyboardController, Key

from utils.logger import get_logger

log = get_logger("text")
kc = KeyboardController()


def _is_ascii(text: str) -> bool:
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def type_text(text: str, cfg: dict | None = None) -> None:
    """输入文字. ASCII 且较短 -> 逐字符; 中文或较长 -> 剪贴板粘贴(保持节奏)"""
    cfg = cfg or {}
    delay_min = cfg.get("delay_min_ms", 40)
    delay_max = cfg.get("delay_max_ms", 120)
    word_pause = cfg.get("word_pause_ms", 150)
    threshold = cfg.get("clipboard_threshold", 20)

    if not text:
        return

    if _is_ascii(text) and len(text) <= threshold:
        log.info("逐字符输入: %r", text[:30])
        for ch in text:
            if ch == "\n":
                kc.press(Key.enter)
                kc.release(Key.enter)
            else:
                kc.type(ch)
            d = random.uniform(delay_min, delay_max)
            if ch == " ":
                d += word_pause
            time.sleep(d / 1000.0)
    else:
        # 剪贴板方式: 中文/长文本
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
        finally:
            root.destroy()
        log.info("剪贴板输入: %r", text[:30])
        kc.press(Key.ctrl)
        kc.press("v")
        kc.release("v")
        kc.release(Key.ctrl)
        time.sleep(word_pause / 1000.0)
