"""观察通道: 放大镜(zoom) + 坐标反算(px_to_screen)。坐标系: 屏幕物理像素。

设计点(见计划 §2.3):
1. 放大插值必须用 NEAREST: LANCZOS/BILINEAR 会把 1px 细线糊成渐变, 模型反而看不清边界
2. 倍率与视野成反比: factor=10 + max_out=1200 只覆盖屏幕 120x120, 故支持显式给坐标做"粗看->精看"
3. 坐标映射必须回传: meta.screen_rect / factor / to_screen / from_screen, 否则模型必然点错

反算(§2.3.1): 服务端记住"最近一次 zoom"的映射, 模型只传放大图里的像素坐标。
"""
import io
import os
import sys
import threading

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENDOR = os.path.join(_BASE, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from PIL import Image, ImageGrab

from input_engine import mouse

try:
    _NEAREST = Image.Resampling.NEAREST      # Pillow >= 9.1
except AttributeError:                       # pragma: no cover - 老版本兜底
    _NEAREST = Image.NEAREST


# ---------- 最近一次观察的映射(反算依赖) ----------

_LAST = None                 # {"meta": dict, "img": PIL.Image} | None
_seq_counter = 0             # 观察序号, 回带给模型核对"看的是哪张图"
_LOCK = threading.Lock()


def _remember(meta: dict, img) -> dict:
    """记下最近一次成功观察的映射(zoom 内部调用)。"""
    global _LAST, _seq_counter
    with _LOCK:
        _seq_counter += 1
        meta["seq"] = _seq_counter
        _LAST = {"meta": meta, "img": img}
    return meta


def last_meta() -> dict | None:
    """最近一次 zoom 的 meta; 没 zoom 过返回 None。"""
    with _LOCK:
        last = _LAST
    return None if last is None else dict(last["meta"])


# ---------- 放大镜 ----------

def clamp_region(cx, cy, src, screen_size):
    """以 (cx,cy) 为中心、边长 src 的矩形, 夹取到屏幕内。返回 (left,top,right,bottom)。"""
    sw, sh = screen_size
    half = src // 2
    left = max(0, min(cx - half, sw - src))
    top = max(0, min(cy - half, sh - src))
    return (left, top, left + src, top + src)


def effective_factor(src, factor, max_out):
    """实际生效倍率: 输出边长不超过 max_out。"""
    f = int(factor)
    while f > 1 and src * f > max_out:
        f -= 1
    return max(1, f)


def zoom(x=None, y=None, factor=10, src=100, quality=85, max_out=1200):
    """放大 (x,y) 周围 src×src 区域; x/y 为 None 时取当前鼠标位置。

    返回 (jpeg_bytes, meta)。成功后本次映射记为"最近一次观察", 供 px_to_screen 复用。
    """
    if x is None or y is None:
        x, y = mouse.position()
    x, y = int(x), int(y)

    img0 = ImageGrab.grab(all_screens=True)
    left, top, right, bottom = clamp_region(x, y, src, img0.size)
    region = img0.crop((left, top, right, bottom))

    f = effective_factor(region.width, factor, max_out)
    big = region.resize((region.width * f, region.height * f), _NEAREST)

    buf = io.BytesIO()
    big.convert("RGB").save(buf, format="JPEG", quality=quality)

    meta = {
        "screen_rect": [left, top, right, bottom],
        "factor": f,
        "src_size": [region.width, region.height],
        "out_size": [big.width, big.height],
        "cursor": [x, y],
        "to_screen": "screen_x = screen_rect[0] + px//factor",
        "from_screen": "px = (screen_x - screen_rect[0]) * factor",
    }
    _remember(meta, big)          # 记住最近一次映射, 供 px_to_screen 反算
    return buf.getvalue(), meta


# ---------- 反算: 放大图像素 -> 屏幕真实坐标(§2.3.1) ----------

def screen_to_px(sx, sy, meta) -> tuple[int, int]:
    """屏幕坐标 -> 放大图像素(取该屏幕像素对应方块的中心), 用于往返自检。"""
    f = meta["factor"]
    left, top = meta["screen_rect"][0], meta["screen_rect"][1]
    return (int((sx - left) * f + f // 2), int((sy - top) * f + f // 2))


def px_to_screen(px, py, meta: dict | None = None) -> tuple[int, int]:
    """放大图像素 -> 屏幕物理像素。meta=None 时用最近一次 zoom 的映射。

    校验: 非整数/超界抛 ValueError(错误信息带真实 out_size); 没有 zoom 记录抛 RuntimeError。
    """
    if meta is None:
        meta = last_meta()
        if meta is None:
            raise RuntimeError("还没有 zoom 记录, 请先调用 zoom")

    try:
        px, py = int(px), int(py)
    except (TypeError, ValueError):
        raise ValueError(f"px/py 必须是整数, 收到 {px!r}/{py!r}")

    ow, oh = meta["out_size"]
    if not (0 <= px < ow and 0 <= py < oh):
        raise ValueError(
            f"像素超出放大图范围: ({px}, {py}) 不在 {ow}x{oh} 内 —— 请确认读的是最近一次 zoom 的图")

    f = meta["factor"]
    left, top = meta["screen_rect"][0], meta["screen_rect"][1]
    sx, sy = left + px // f, top + py // f
    # 整除已保证落在 screen_rect 内, 这里只做最后一道夹取兜底
    right, bottom = meta["screen_rect"][2] - 1, meta["screen_rect"][3] - 1
    return (min(sx, right), min(sy, bottom))
