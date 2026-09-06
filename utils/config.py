"""配置系统: 加载 config.json, 与默认配置深层合并, 缺失项自动补默认"""
import json
import os
from copy import deepcopy

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "hotkeys": {
        "game": "ctrl+shift+1",
        "draw": "ctrl+shift+2",
        "work": "ctrl+shift+3",
        "quit": "ctrl+shift+0",
    },
    "mouse": {
        "move_steps": 24,          # 直线移动插值步数
        "move_interval_ms": 5,     # 每步间隔
        "jitter_px": 1.2,          # 抖动幅度(像素, 高斯分布)
        "jitter_enabled": True,    # 是否启用抖动
        "accel_curve": True,       # 加速-减速曲线
    },
    "game": {
        "tap_interval_ms": 180,    # 连点间隔
        "tap_jitter_ms": 30,       # 连点间隔抖动
        "slide": {                 # 直线滑动(游戏内视角移动)
            "smooth_ms": 800,      # 目标滑动时长
            "jitter_px": 2.0,      # 抖动幅度
            "curve_bend": 0.06,    # 轻微弯曲比例(0=纯直线)
        },
        "macros": {                # 宏触发器: 名称 -> {hotkey, type, ...}
            "连点攻击":  {"hotkey": "f6", "type": "tap_repeat", "key": "space", "times": 3},
            "视角左拉":  {"hotkey": "f7", "type": "slide", "dx": -250, "dy": 0},
            "视角右拉":  {"hotkey": "f8", "type": "slide", "dx": 250, "dy": 0},
            "复制粘贴":  {"hotkey": "f9", "type": "combo", "keys": ["ctrl", "c"]},
        },
    },
    "draw": {
        "sample_min_dist": 2,      # 采样最小位移(px)
        "sample_interval_ms": 8,   # 采样最小间隔(ms)
        "rdp_epsilon": 4,          # RDP 简化容差
        "bezier_segments": 8,      # 每段贝塞尔细分点数
        "replay_interval_ms": 8,   # 重放注入间隔
        "replay_speed": 1.0,       # 重放速度倍率
        "brush": {
            "modifier": "alt",     # 画笔大小修饰键
            "step": 2,             # 滚轮每格增减
            "min": 1,
            "max": 200,
        },
        "pen": {
            "pressure_min": 50,    # 最轻压力(0-1024)
            "pressure_max": 1024,  # 最重压力
            "slow_speed": 50,      # 低于此速度视为重压(px/s)
            "fast_speed": 500,     # 高于此速度视为轻扫(px/s)
            "smooth_window": 4,    # 压力移动平均窗口
            "toggle_key": "p",     # 鼠标/笔输入切换键
        },
    },
    "work": {
        "typing": {
            "delay_min_ms": 40,    # 字符间隔下限
            "delay_max_ms": 120,   # 字符间隔上限
            "word_pause_ms": 150,  # 词间停顿
            "clipboard_threshold": 20,  # 超过此长度用剪贴板
        },
        "shortcuts": {             # 简化快捷键
            "copy": ["ctrl", "c"],
            "paste": ["ctrl", "v"],
            "cut": ["ctrl", "x"],
            "undo": ["ctrl", "z"],
            "select_all": ["ctrl", "a"],
            "switch_win": ["alt", "tab"],
            "show_desktop": ["win", "d"],
            "lock": ["win", "l"],
        },
    },
    "ai": {
        "base_url": "https://api.deepseek.com",   # 默认与DSH对话相同(deepseek-official); 可改官方/本地兼容API
        "api_key": "",            # 留空则读环境变量 DEEPSEEK_API_KEY / TOKENRHYTHM_API_KEY
        "model": "deepseek-v4-flash-vision-exp",  # 默认与DSH对话相同(支持视觉); 可手动改其他
        "max_steps": 20,           # 单任务最大动作步数
        "screenshot_scale": 0.6,   # 截图缩放(降token)
        "screenshot_quality": 60,  # JPEG 质量
        "whitelist": [             # 模型可修改的配置键(白名单)
            "mouse.jitter_px", "mouse.move_steps", "mouse.move_interval_ms",
            "game.tap_interval_ms", "game.slide.jitter_px", "game.slide.smooth_ms",
            "draw.brush.step", "draw.replay_interval_ms",
            "work.typing.delay_min_ms", "work.typing.delay_max_ms"
        ],
    },
}

def _deep_merge(base: dict, override: dict) -> dict:
    """override 递归覆盖 base, 返回新字典"""
    out = deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out

def load_config(path: str = CONFIG_PATH) -> dict:
    """加载配置, 不存在则写默认"""
    if not os.path.exists(path):
        save_config(DEFAULT_CONFIG, path)
        return deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        return _deep_merge(DEFAULT_CONFIG, user_cfg)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[config] 读取失败({e}), 使用默认配置")
        return deepcopy(DEFAULT_CONFIG)

def save_config(cfg: dict, path: str = CONFIG_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
