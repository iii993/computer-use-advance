"""AI 指挥官: 截图 -> 模型决策 -> 调操控服务执行(识图能力检测/白名单自改)

模型通过 OpenAI 兼容 API 接入 (base_url 可指向官方或本地 Ollama 等)。
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


def _read_dsh_credentials(name: str) -> str:
    """从 DSH 凭据文件(H:/dsh-home/.credentials.yaml)的 refs 段读取密钥.

    轻量解析: 只匹配 refs: 段下的 "  NAME: value" 行, 无需 yaml 依赖.
    """
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
            # 离开 refs 段: 遇到顶格或非缩进的行
            if stripped and line[0] != " " and stripped != "refs:":
                break
            if stripped.startswith(name + ":"):
                return stripped.split(":", 1)[1].strip().strip("'\"")
    return ""

SYSTEM_PROMPT = """你是电脑操控 AI。你通过截图观察屏幕, 调用动作完成任务。
可用动作(每次只输出一个 JSON):
{"action":"click","x":<像素>,"y":<像素>}           点击
{"action":"double_click","x":..,"y":..}           双击
{"action":"right_click","x":..,"y":..}            右键
{"action":"drag","x1":..,"y1":..,"x2":..,"y2":..} 拖拽
{"action":"type_text","text":"..."}               输入文字(中文自动剪贴板)
{"action":"press_key","key":"space"}              按键(enter/tab/esc/f1-f12/ctrl..)
{"action":"combo","keys":["ctrl","c"]}            组合键
{"action":"slide","dx":..,"dy":..}                相对滑动(游戏视角, 带防检测抖动)
{"action":"switch_mode","mode":"game|draw|work"}  切换模式
{"action":"set_config","key":"mouse.jitter_px","value":1.5}  修改配置(仅白名单)
{"action":"finish","result":"任务完成说明"}         完成任务
规则:
- 坐标基于你看到的截图, 图片已缩放, 坐标需换算回真实屏幕(缩放比见提示)
- 操作后如需确认效果, 可再次截图观察
- 不要臆想屏幕内容, 以截图为准
- 任务完成后输出 finish"""


class VisionError(Exception):
    """模型无识图能力或 API 配置错误"""


class AIController:
    def __init__(self, service, config: dict | None = None):
        self.service = service
        self.config = config or service.config
        self.scale = float(self.config.get("ai", {}).get("screenshot_scale", 0.6))
    # ---------- API 调用 ----------
    def _api(self, messages: list, max_tokens: int = 600) -> dict:
        ai_cfg = self.config.get("ai", {})
        base_url = (ai_cfg.get("base_url") or "").rstrip("/")
        model = ai_cfg.get("model") or ""
        # api_key 优先级: config.json > 环境变量 > DSH 凭据文件(与对话相同)
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
            with urllib.request.urlopen(req, timeout=60) as resp:
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
        """发 1x1 测试图, API 接受图像输入 -> 有识图能力; 拒绝 -> 报错"""
        try:
            self._api([{
                "role": "user",
                "content": self._content(
                    "这张图片是什么颜色? 只回答颜色名。",
                    TEST_IMAGE_B64,
                ),
            }], max_tokens=50)
            log.info("识图检测通过: API 接受图像输入")
            return {"ok": True, "detail": "API 接受图像输入"}
        except VisionError as e:
            log.error("识图检测失败: %s", e)
            return {"ok": False, "error": str(e)}

    # ---------- 任务执行 ----------
    def run_task(self, task: str, max_steps: int | None = None, on_step=None) -> dict:
        """执行任务: 循环 [截图 -> 模型 -> 动作]"""
        max_steps = max_steps or int(self.config.get("ai", {}).get("max_steps", 20))
        self.service.reload_config()
        self.config = self.service.config
        self.scale = float(self.config.get("ai", {}).get("screenshot_scale", 0.6))

        # 先检测识图能力
        vision = self.check_vision()
        if not vision["ok"]:
            return {"ok": False, "error": "模型无识图能力: " + vision["error"], "steps": 0}

        state = json.dumps(self.service.get_state(), ensure_ascii=False)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._content(
                "任务: " + task + "\n截图已缩放 " + format(self.scale, ".2f") +
                " 倍, 坐标需除以缩放比换算为真实屏幕坐标。当前状态: " + state
            )},
        ]

        for step in range(1, max_steps + 1):
            img = self.service.screenshot()
            img_b64 = base64.b64encode(img).decode()
            if on_step:
                on_step("screenshot", step)

            messages.append({"role": "user", "content": self._content("截图:", img_b64)})
            try:
                resp = self._api(messages, max_tokens=600)
            except VisionError as e:
                return {"ok": False, "error": str(e), "steps": step}
            reply = resp["choices"][0]["message"]["content"] or ""
            messages.append({"role": "assistant", "content": reply})

            action = self._parse_action(reply)
            if not action:
                log.warning("第%d步模型输出无法解析: %r", step, reply[:200])
                continue
            log.info("第%d步: %s", step, action)

            if action.get("action") == "finish":
                return {"ok": True, "result": action.get("result", ""), "steps": step}

            result = self._execute(action)
            messages.append({"role": "user", "content": json.dumps(
                {"动作结果": result}, ensure_ascii=False)})

        return {"ok": False, "error": "超过最大步数(" + str(max_steps) + ")未完成", "steps": max_steps}

    # ---------- 动作解析与执行 ----------
    def _parse_action(self, reply: str) -> dict | None:
        text = reply.strip()
        if "\u0060\u0060\u0060" in text:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
        return None

    def _execute(self, action: dict) -> dict:
        svc = self.service
        act = action.get("action")
        try:
            if act == "click":
                svc.click(action.get("x", 0) / self.scale, action.get("y", 0) / self.scale)
            elif act == "double_click":
                svc.click(action.get("x", 0) / self.scale, action.get("y", 0) / self.scale,
                          clicks=2)
            elif act == "right_click":
                svc.click(action.get("x", 0) / self.scale, action.get("y", 0) / self.scale,
                          button="right")
            elif act == "drag":
                svc.drag(action["x1"] / self.scale, action["y1"] / self.scale,
                         action["x2"] / self.scale, action["y2"] / self.scale)
            elif act == "type_text":
                svc.type_text(action.get("text", ""))
            elif act == "press_key":
                svc.press_key(action.get("key", ""))
            elif act == "combo":
                svc.combo(action.get("keys", []))
            elif act == "slide":
                svc.slide(action.get("dx", 0), action.get("dy", 0))
            elif act == "switch_mode":
                return svc.switch_mode(action.get("mode", ""))
            elif act == "set_config":
                return svc.set_config(action.get("key", ""), action.get("value"))
            else:
                return {"ok": False, "error": "未知动作: " + str(act)}
            return {"ok": True}
        except KeyError as e:
            return {"ok": False, "error": "缺少参数: " + str(e)}
        except Exception as e:
            return {"ok": False, "error": "执行失败: " + str(e)}
