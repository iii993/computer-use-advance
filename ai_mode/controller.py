"""AI 指挥官 v2: 对齐 codex computer-use 架构

核心改进:
- 批量动作数组: 模型一次返回多个动作, 全部执行后再截图反馈
- 滑动窗口上下文: 截图只保留最近 3 轮, 历史动作用文本记录
- 细粒度动作空间: click/drag/wait/scroll/key_down/up/hotkey 等
- 解析容错: 输出无法解析时把错误回传模型修正
- 截图策略: 首次 + 每批动作后自动反馈 + 模型可显式 take_screenshot
"""
import base64
import json
import os
import urllib.error
import urllib.request

from utils.logger import get_logger

log = get_logger("ai")

# 1x1 红色测试图(最小 PNG), 用于识图能力检测
TEST_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# 滑动窗口: 最多保留最近 N 轮截图(其余替换为文本说明, 防止上下文爆炸)
MAX_SCREENSHOT_TURNS = 3


def _read_dsh_credentials(name: str) -> str:
    """从 DSH 凭据文件(H:/dsh-home/.credentials.yaml)的 refs 段读取密钥. 轻量解析, 无需 yaml 依赖."""
    dsh_home = os.environ.get("DSH_HOME", r"H:\dsh-home")
    path = os.path.join(dsh_home, ".credentials.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return ""
    in_refs = False
    for line in lines:
        stripped = line.strip()
        if stripped == "refs:":
            in_refs = True
            continue
        if in_refs:
            if stripped and line[0] != " " and stripped != "refs:":
                break
            if stripped.startswith(name + ":"):
                return stripped.split(":", 1)[1].strip().strip('\"\'')
    return ""


class VisionError(Exception):
    """模型无识图能力或 API 配置错误"""


SYSTEM_PROMPT = """你是电脑操控 AI。你通过截图观察屏幕, 调用动作完成任务。
输出格式: 只输出一个 JSON 对象, 格式为 {"actions": [...]}, actions 数组按顺序执行。
可用动作:
{"action":"click","x":..,"y":..}                 单击
{"action":"double_click","x":..,"y":..}          双击
{"action":"right_click","x":..,"y":..}           右键
{"action":"drag","x1":..,"y1":..,"x2":..,"y2":..} 拖拽
{"action":"mouse_down","x":..,"y":..}            按下左键(配合mouse_up拖动画框)
{"action":"mouse_up","x":..,"y":..}              释放左键
{"action":"scroll","x":..,"y":..,"dy":-300}       移动到(x,y)并滚轮(dy>0上滚)
{"action":"type_text","text":"..."}              输入文字(中文自动剪贴板)
{"action":"press_key","key":"enter"}             单击按键
{"action":"key_down","key":"ctrl"}               按住按键(配合key_up实现长按)
{"action":"key_up","key":"ctrl"}                 释放按键
{"action":"hotkey","keys":["ctrl","s"]}          组合键
{"action":"slide","dx":..,"dy":..}               相对滑动(游戏模式防检测)
{"action":"switch_mode","mode":"game|draw|work"} 切换模式
{"action":"set_config","key":"mouse.jitter_px","value":1.5} 修改配置(仅白名单)
{"action":"wait","seconds":2}                    等待(页面加载/动画)
{"action":"take_screenshot"}                     主动截图观察
{"action":"finish","result":"任务完成说明"}       完成任务
规则:
- actions 数组按顺序执行, 可一次返回多个动作(减少往返)
- 每批动作执行后系统会自动截图反馈新状态
- 截图使用原始分辨率, 坐标与真实屏幕一致, 直接使用
- 操作后如需确认效果可加 take_screenshot
- 不要臆想屏幕内容, 以截图为准
- 任务完成后输出 finish"""


class AIController:
    def __init__(self, service, config: dict | None = None):
        self.service = service
        self.config = config or service.config

    # ---------- API 调用 ----------
    def _api(self, messages: list, max_tokens: int = 800) -> dict:
        ai_cfg = self.config.get("ai", {})
        base_url = (ai_cfg.get("base_url") or "").rstrip("/")
        model = ai_cfg.get("model") or ""
        api_key = (ai_cfg.get("api_key") or ""
                   or os.environ.get("DEEPSEEK_API_KEY") or ""
                   or os.environ.get("TOKENRHYTHM_API_KEY") or ""
                   or _read_dsh_credentials("DEEPSEEK_API_KEY") or ""
                   or _read_dsh_credentials("TOKENRHYTHM_API_KEY") or "")
        if not base_url or not model:
            raise VisionError("未配置 ai.base_url / ai.model, 无法启动 AI 模式")

        url = base_url + "/chat/completions"
        body = json.dumps({
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if api_key:
            req.add_header("Authorization", "Bearer " + api_key)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (400, 422, 404):
                raise VisionError("API 拒绝请求(" + str(e.code) + "): " + detail) from e
            raise
        except urllib.error.URLError as e:
            raise VisionError("无法连接 API: " + str(e)) from e
        return data

    def _content(self, text: str, image_b64: str | None = None) -> list:
        parts = [{"type": "text", "text": text}]
        if image_b64:
            parts.append({"type": "image_url",
                          "image_url": {"url": "data:image/jpeg;base64," + image_b64}})
        return parts

    # ---------- 识图能力检测 ----------
    def check_vision(self) -> dict:
        try:
            self._api([{
                "role": "user",
                "content": self._content("这张图片是什么颜色? 只回答颜色名。", TEST_IMAGE_B64),
            }], max_tokens=50)
            log.info("识图检测通过: API 接受图像输入")
            return {"ok": True, "detail": "API 接受图像输入"}
        except VisionError as e:
            log.error("识图检测失败: %s", e)
            return {"ok": False, "error": str(e)}

    # ---------- 上下文管理(滑动窗口) ----------
    def _compact_screenshots(self, messages: list) -> None:
        """截图只保留最近 MAX_SCREENSHOT_TURNS 轮, 更早的替换为文本说明."""
        seen = 0
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    if seen >= MAX_SCREENSHOT_TURNS:
                        part["type"] = "text"
                        part["text"] = "[此处省略早期截图]"
                        del part["image_url"]
                    else:
                        seen += 1

    # ---------- 任务执行 ----------
    def run_task(self, task: str, max_steps: int | None = None, on_step=None) -> dict:
        max_steps = max_steps or int(self.config.get("ai", {}).get("max_steps", 20))
        self.service.reload_config()
        self.config = self.service.config

        # 先检测识图能力
        vision = self.check_vision()
        if not vision["ok"]:
            return {"ok": False, "error": "模型无识图能力: " + vision["error"], "steps": 0}

        state = json.dumps(self.service.get_state(), ensure_ascii=False)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._content(
                "任务: " + task + "\n截图使用原始分辨率, 坐标与真实屏幕一致。当前状态: " + state
            )},
        ]

        for step in range(1, max_steps + 1):
            # 首次或上批动作后: 截图反馈
            img_b64 = base64.b64encode(self.service.screenshot()).decode()
            if on_step:
                on_step("screenshot", step)
            messages.append({"role": "user", "content": self._content("截图:", img_b64)})
            self._compact_screenshots(messages)

            # 模型决策
            try:
                resp = self._api(messages, max_tokens=800)
            except VisionError as e:
                return {"ok": False, "error": str(e), "steps": step}
            reply = resp["choices"][0]["message"]["content"] or ""
            messages.append({"role": "assistant", "content": reply})

            actions = self._parse_actions(reply)
            if actions is None:
                # A4 容错: 把解析错误回传模型, 让它修正(不消耗太多步数)
                log.warning("第%d步模型输出无法解析: %r", step, reply[:200])
                messages.append({"role": "user", "content":
                                 '你的输出无法解析为 JSON, 请只输出一个 {"actions": [...]} 对象: ' + reply[:200]})
                continue

            # 执行批量动作(A1/A5: 批内连续执行无多余延迟)
            results = []
            finished = False
            for act in actions:
                if act.get("action") == "finish":
                    finished = True
                    results.append({"action": "finish", "result": act.get("result", "")})
                    break
                log.info("第%d步: %s", step, act)
                results.append(self._execute(act))
                if on_step:
                    on_step(act.get("action", "?"), step)

            if finished:
                return {"ok": True, "result": results[-1].get("result", ""), "steps": step}

            messages.append({"role": "user", "content": json.dumps(
                {"本批动作结果": results}, ensure_ascii=False)})

        return {"ok": False, "error": "超过最大步数(" + str(max_steps) + ")未完成", "steps": max_steps}

    # ---------- 批量动作解析 ----------
    def _parse_actions(self, reply: str) -> list | None:
        """解析模型输出为动作列表. 支持 {"actions":[...]} 或单个 {...}."""
        text = reply.strip()
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    return None
            else:
                return None
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            acts = data.get("actions")
            if isinstance(acts, list):
                return acts
            if "action" in data:
                return [data]
        return None

    # ---------- 动作执行 ----------
    def _execute(self, action: dict) -> dict:
        svc = self.service
        act = action.get("action")
        try:
            if act == "click":
                svc.click(action.get("x", 0), action.get("y", 0))
            elif act == "double_click":
                svc.click(action.get("x", 0), action.get("y", 0), clicks=2)
            elif act == "right_click":
                svc.click(action.get("x", 0), action.get("y", 0), button="right")
            elif act == "drag":
                svc.drag(action["x1"], action["y1"], action["x2"], action["y2"])
            elif act == "mouse_down":
                svc.mouse_down(action.get("x", 0), action.get("y", 0))
            elif act == "mouse_up":
                svc.mouse_up(action.get("x", 0), action.get("y", 0))
            elif act == "scroll":
                svc.scroll(action.get("x"), action.get("y"),
                           action.get("dx", 0), action.get("dy", -300))
            elif act == "type_text":
                svc.type_text(action.get("text", ""))
            elif act == "press_key":
                svc.press_key(action.get("key", ""))
            elif act == "key_down":
                svc.key_down(action.get("key", ""))
            elif act == "key_up":
                svc.key_up(action.get("key", ""))
            elif act == "hotkey":
                svc.hotkey(action.get("keys", []))
            elif act == "combo":
                svc.combo(action.get("keys", []))
            elif act == "slide":
                svc.slide(action.get("dx", 0), action.get("dy", 0))
            elif act == "switch_mode":
                return svc.switch_mode(action.get("mode", ""))
            elif act == "set_config":
                return svc.set_config(action.get("key", ""), action.get("value"))
            elif act == "wait":
                svc.wait(action.get("seconds", 1))
            elif act == "take_screenshot":
                return {"ok": True, "note": "将在本批动作后自动截图反馈"}
            else:
                return {"ok": False, "error": "未知动作: " + str(act)}
            return {"ok": True}
        except KeyError as e:
            return {"ok": False, "error": "缺少参数: " + str(e)}
        except Exception as e:
            return {"ok": False, "error": "执行失败: " + str(e)}
