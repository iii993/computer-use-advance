# 控制系统重构 + 观察通道(放大镜 / UIA) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`[ ]`) syntax for tracking.

**Goal:** 把 `input_engine` / `computer_core` 的输入控制重构为"移动与点击彻底解耦 + 点击可指定按压时长/按键种类(含侧键)/双击",移动/点击均可按坐标序列批量执行并设置步间间隔,并新增两条观察通道:10X 鼠标放大镜(含"放大图像素 → 屏幕真实坐标"反算)与 UIA 无障碍树文本观察。

**Architecture:** 输入层(`input_engine/`)只负责原语,不再隐含"移动+点击"的复合行为;`computer_core/service.py` 作为统一服务层适配新旧调用并保持向后兼容;观察能力拆成独立模块 `computer_core/observe.py`(放大镜,基于 Pillow)与 `computer_core/uia.py`(基于纯 ctypes 直调 Windows UI Automation,零第三方依赖),最后在 `computer_mcp/server.py` 暴露为 MCP 工具。输入层与服务层同时支持"坐标序列 + 步间间隔"的批量执行;放大镜在 `observe.py` 内保存最近一次观察的坐标映射,供反算复用。

**Tech Stack:** Python 3.14.3 / pynput 1.8.2 / Pillow 12.3.0 / 纯 ctypes (UIAutomationCore.dll) / MCP stdio (JSON-RPC)

**Spec:** 本文档自包含(设计 + 实测事实 + 实施步骤)。相关前序文档:`docs/superpowers/plans/2026-09-05-computer-control-plugin.md`

## Global Constraints

以下约束对所有任务生效,数值逐字取自实测或现有代码:

- 平台: Windows 10/11;开发环境 Python **3.14.3**;PyPI 依赖一律装进 `H:\cu-a\vendor`(项目根 `vendor/`),**不得写入 C 盘**。
- 现有依赖(已存在,无需再装):`pynput 1.8.2`、`Pillow 12.3.0`、`pystray 0.19.5`、`keyboard 0.13.5`、`six 1.17.0`。
- **不新增任何第三方依赖**。UIA 通道必须用标准库 `ctypes` 实现,禁止引入 `uiautomation` / `comtypes` / `pywinauto`。
- **向后兼容是硬要求**:`computer_mcp/server.py` 现有的 21 个工具名与参数不得改名或改语义;`ai_mode/controller.py` 的 `SYSTEM_PROMPT` 里已列出的 19 种动作不得删除。新增能力只能"加参数 / 加工具"。
- 提交规范:中文 commit message,一次提交只含一个逻辑改动;临时文件、`vendor/`、`__pycache__` 不得入库。
- **诚实原则**:本文档把"实测验证过"与"未打通"分开标注。凡未验证的 vtable 索引,实施时**必须重新实测**,不得直接采信。

---

## 1. 背景与现状

### 1.1 当前代码基线

| 文件 | 现状 | 问题 |
|---|---|---|
| `input_engine/mouse.py` | `mc = MouseController()`;`move_absolute()` 瞬移;`move_to()` 直线插值 + 高斯抖动 + 加速曲线;`move_relative()`;曲线相关函数 | 移动与点击语义混在一起,没有统一的"只移动"入口 |
| `input_engine/keyboard.py` | `press/tap/combo/release` | 本次不动 |
| `computer_core/service.py` | `click(x, y, button="left", clicks=1)` **内部先 `move_absolute` 再点**;`drag`、`scroll`、`mouse_down/up`、`screenshot`、`screenshot_model`、`screenshot_png`、`get_state`、`set_config` | `click` 耦合移动;无法指定按压时长;无法用侧键;双击间隔不可控 |
| `computer_mcp/server.py` | 21 个 MCP 工具 + `run_actions` 批量;坐标用"截图内像素",server 侧按最近一次截图尺寸换算回屏幕坐标 | 观察只有截图一条路 |
| `ai_mode/controller.py` | AI 指挥官 v2:批量动作 + 3 轮截图滑动窗口 + 识图检测 + 解析容错 | `SYSTEM_PROMPT` 的动作用例是兼容性契约 |
| `config.json` | `mouse.move_steps=24`、`move_interval_ms=5`、`jitter_px=1.2`、`accel_curve=true`;`work.typing.*`;`ai.*` | 新增参数需带默认值,缺省合并由 `utils/config.py` 负责 |

### 1.2 本次要解决的四个问题

1. **移动与点击耦合**:AI 无法"先移动到目标 → 截图确认 → 再点击"。现有 `click` 一步做完,期间画面可能已变。
2. **点击能力单一**:只有 `clicks=1/2` 和 `left/right/middle`。缺按压时长(长按/拖住)、侧键(X1/X2)、可控双击间隔。
3. **观察通道单一**:只有全屏截图。缺"看清小目标"的能力(放大镜),也缺"精确知道有哪些窗口/控件"的能力(UIA 文本)。
4. **批量执行与坐标反算缺失**:`move`/`click` 一次只能处理一个坐标,要让鼠标依次走过/点过一串位置,模型只能反复往返(慢且耗 token),也没有"步间间隔"可调;放大镜虽然给了放大图,却没有把**放大图里的像素**换算回**屏幕真实坐标**的手段,模型看完细节仍然点不准。

---

## 2. 设计

### 2.1 输入层:移动与点击彻底解耦

`input_engine/mouse.py` 新增两个显式入口,**不删除**任何现有函数(避免破坏调用方):

```python
# 按键映射(侧键已实测支持)
BUTTONS = {
    "left": Button.left,
    "right": Button.right,
    "middle": Button.middle,
    "x1": Button.x1,   # 侧键1(通常"后退")
    "x2": Button.x2,   # 侧键2(通常"前进")
}

HOLD_MS_MAX = 5000               # 单次按压上限,防模型给超大值卡死
DOUBLE_CLICK_INTERVAL_MS = 80    # 双击两次 down/up 的间隔


def move(x=None, y=None, mode="smooth", duration_ms=None,
         points=None, gap_ms=0, cfg=None):
    """只移动,不点击。
    mode='smooth'(插值+抖动+加速) | 'instant'(瞬移)。
    单点: move(100, 200, mode="smooth", duration_ms=500)
    序列: move(points=[[100, 200], [300, 400, 300]], gap_ms=120)
    """


def click(button="left", x=None, y=None, hold_ms=0, clicks=1, interval_ms=None,
          at=None, points=None, gap_ms=0, cfg=None):
    """只点击(给了 x/y / at / points 才会先移动)。
    x/y 保留在原有位置参数上: click("right", 100, 200) 与旧调用逐字兼容;
    新增参数一律走关键字(实施偏差, 见 §8)。
    单点: click(x=100, y=200, hold_ms=120)        # at 形式: 先瞬移再点
    序列: click(points=[[100, 200], [300, 400, 60]], button="left", gap_ms=200)
    """
```

行为定义:

- `move(mode="smooth")` 复用现有 `move_to()`;`move(mode="instant")` 等价现有 `move_absolute()`。
- `click(hold_ms=0)` 走 `mc.click(btn, 1)`;`hold_ms>0` 走 `press → sleep(hold_ms/1000) → release`。
- `hold_ms` 越界(负数或 > `HOLD_MS_MAX`)**抛 `ValueError`**,不静默夹取。
- `clicks>1` 时两次点击之间 `sleep(interval_ms/1000)`,`interval_ms` 默认 `DOUBLE_CLICK_INTERVAL_MS`。
- 未知 `button` 抛 `ValueError`,错误信息里列出全部合法值。

**四个"时间/间隔"参数含义互不重叠(命名规则,勿混):**

| 参数 | 作用域 | 默认 | 说明 |
|---|---|---|---|
| `duration_ms` | move 的**单次移动** | `None`(退回 config 的 `move_steps × move_interval_ms`) | 整段移动耗时;仅 `mode="smooth"` 生效,`instant` 忽略 |
| `hold_ms` | click 的**单次按压** | `0` | `press → sleep(hold_ms) → release`;0 表示走 `mc.click()` 快速点击 |
| `interval_ms` | click **内部**多次 down/up | `80`(`DOUBLE_CLICK_INTERVAL_MS`) | 即双击间隔,只在 `clicks>1` 时生效,**与序列无关** |
| `gap_ms` | **序列相邻两点之间** | `0` | 只在 `points` 模式生效;`move`/`click` 同名同义 |

> `interval_ms`(点击内部)与 `gap_ms`(序列之间)是两个不同的东西,故意不同名,避免"双击间隔"和"步间间隔"互相污染。

**坐标序列(`points`)规则:**

- `points` 与 `x`/`y`/`at` **互斥**(同时给抛 `ValueError`);都不给时:move 抛 `ValueError`(没有目标),click 表示"原地点击当前位置"(保持旧的原地点击语义)。
- 每个元素是 `[x, y]` 两元或 `[x, y, t]` 三元;三元时第三个数**覆盖本点**的 `duration_ms`(move)/ `hold_ms`(click),两元时用函数级参数。这与项目里 `draw_pressure_curve` 的"三维点 `[x, y, 压力]`"约定同构(第三位 = 本点强度/时长)。
- 校验:元素个数为 0、元素长度不是 2/3、元素含非数字、`gap_ms < 0`、三元点的时间值越界(move 为负 / click 超出 `HOLD_MS_MAX`)一律抛 `ValueError`(与 `hold_ms` 越界的处理风格一致,不静默夹取)。
- 执行节奏:点1 → `sleep(gap_ms)` → 点2 → ……;最后一个点之后**不**额外 sleep。
- `points` 与 `clicks` 可组合:序列模式下每个点都按 `clicks` 次点击(常规场景保持 `clicks=1`)。
- 返回值:输入层仍返回 `None`(原语风格);**服务层**返回执行记录 `{"count": n, "points": [[sx, sy], ...], "gap_ms": g, "duration_ms": d}`,便于模型核对"我到底走了哪些点"。

**保持简单(向后兼容底线):** `move(100, 200)` / `move(100, 200, duration_ms=500)` / `click(100, 200)` / `click(100, 200, button="right", clicks=2)` 全部照旧;序列只是可选语法糖,不传 `points` 时新旧行为完全一致。

> **双击语义说明**:Windows 判定双击依赖 `GetDoubleClickTime()`(默认 500ms)。本实现固定 80ms 间隔,**远小于系统阈值**,因此系统会识别为双击。这一点写进工具描述,避免模型猜。

### 2.2 服务层适配(`computer_core/service.py`)

`ComputerService.click` 保持旧签名可用,新增可选参数:

```python
def move(self, x=None, y=None, mode="smooth", duration_ms=None,
         points=None, gap_ms=0) -> dict:
    """只移动, 不点击。单点或坐标序列(见 2.1)。返回执行记录。"""


def click(self, x=None, y=None, button="left", clicks=1,
          hold_ms=0, interval_ms=None, move_mode="instant",
          points=None, gap_ms=0) -> dict:
    """at 语义: x/y 都给则先移动(默认瞬移);都不给则原地点击。
    points 给了则按序列执行(与 x/y 互斥)。返回执行记录。"""
```

- **移动与点击彻底独立**:`click` 不带坐标时**不做任何移动**;`move` **永不点击**。要"移过去再点"有三种写法——① 两步 `move(...)` → (可选截图确认) → `click(...)`,这是推荐给 AI 的写法(中间可以观察);② 一次 `click(x, y)`(内部瞬移,仅为兼容旧调用保留);③ 一次 `click(x, y, move_mode="smooth")`(先平滑移动再点,等价于 ① 但没有观察间隙)。
- `move` 的 `duration_ms` 与 `click` 的 `hold_ms` 就是两者各自的"执行时间",都可单点设置或按点覆盖(`[x, y, t]` 三元)。
- 序列模式下服务层统一走 `input_engine.mouse` 的同一套校验,服务层不再重复实现节奏控制。

新增薄封装:`zoom_tool(x, y, factor, src)`(见 2.3;供 MCP 用,返回 `(jpeg_bytes, meta)` 并把 meta 记为"最近一次观察")、`zoom_to_screen(px, py)`(见 2.3.1)、`describe_windows()`(见 2.4)。

**兼容验证点**:现有调用 `svc.click(x, y, button="right", clicks=2)` 必须行为不变(先瞬移再双击);`svc.click()` 仍为原地点击。

### 2.3 放大镜观察工具(默认 10X)

新模块 `computer_core/observe.py`,核心函数:

```python
def zoom(x=None, y=None, factor=10, src=100, quality=85, max_out=1200):
    """放大以 (x,y) 为中心、边长 src 的屏幕区域。
    返回 (jpeg_bytes, meta)。x/y 为 None 时取当前鼠标位置。
    """
```

三个**必须遵守**的设计点(理由见 §3.10):

1. **缩放插值必须用 `Image.NEAREST`**。用 LANCZOS/BILINEAR 会把 1px 细线糊成渐变,模型反而看不清边界。
2. **倍率与视野成反比**。`factor=10` 且输出限制在 `max_out=1200` 时,实际只覆盖屏幕 `120×120` 区域。因此工具必须支持模型显式给坐标(`x, y`),形成"全屏截图粗定位 → zoom 精看"的两级工作流;仅靠鼠标当前位置是不够的。
3. **必须把坐标映射回传给模型**。返回的 `meta` 固定包含:

```python
{
  "screen_rect": [left, top, right, bottom],   # 这块图对应屏幕的哪个矩形
  "factor": 10,                                 # 实际生效倍率(可能被 max_out 下调)
  "src_size": [w, h],                           # 原始区域尺寸
  "out_size": [out_w, out_h],                   # 返回图尺寸
  "cursor": [x, y],                             # 观察中心的屏幕坐标
  "seq": 3,                                     # 本次观察序号(自增), 反算时原样回带, 便于核对用的是哪张图
  "to_screen": "screen_x = screen_rect[0] + px/factor",
  "from_screen": "px = (screen_x - screen_rect[0]) * factor"
}
```

否则模型拿放大图里的像素坐标直接去点击,必然全错。

边界处理:`bbox` 超出屏幕时要夹取到屏幕内(多屏用 `all_screens=True`),并把**夹取后的**真实矩形写进 `screen_rect`。

### 2.3.1 反算:放大图像素 → 屏幕真实坐标

**动机**:模型看着放大图说"我要点这条线左边第二格",它只会得到**放大图里的像素**。没有反算,模型只能靠心算 `screen_rect[0] + px/factor`,一步算错就点飞。

**接口(服务端记住最近一次 zoom,模型只传两个数):**

```python
# computer_core/observe.py
_LAST = None          # 最近一次成功的 zoom: {"meta": dict, "img": PIL.Image | None}; 配一个模块级 _LOCK

def px_to_screen(px, py, meta=None) -> tuple[int, int]:
    """放大图像素 → 屏幕物理像素。meta 为 None 时用最近一次 zoom 的 meta。
    纯函数(给定 meta 时), 便于单测。
    """

def last_meta() -> dict | None:
    """最近一次 zoom 的 meta; 没 zoom 过返回 None。"""
```

```python
# computer_core/service.py
def zoom_to_screen(self, px, py) -> dict:
    """用最近一次 zoom 的映射反算。返回 {"screen_x":.., "screen_y":.., "seq":.., "factor":..}"""
```

**语义与校验(必须按此实现,避免模型算错还拿到一个"看似有效"的数):**

1. 公式:`screen_x = screen_rect[0] + px // factor`,`screen_y = screen_rect[1] + py // factor`。**整除**而非浮点,因为倍率是整数、`Image.NEAREST` 下放大图里 `factor×factor` 个像素对应屏幕 1 像素。
2. 合法像素(`0 ≤ px < out_size[0]`、`0 ≤ py < out_size[1]`)的整除结果必然落在 `screen_rect` 内;实现里仍做一次 `min` 夹取兜底,防止将来改倍率算法后越界。
3. `px`/`py` 超出 `out_size` → 抛 `ValueError`,错误信息带上真实的 `out_size`(让模型知道它读错图了),**不静默夹取**。
4. 没有任何 zoom 记录 → 抛 `RuntimeError("还没有 zoom 记录, 请先调用 zoom")` —— 不返回猜测值。
5. 返回值回带 `seq`,与 meta 的 `seq` 对照,模型可自检"我看的是哪张图"。
6. 线程安全:zoom 的写入与反算的读取共用一个 `threading.Lock`(MCP 单线程也会被 AI 任务窗口并发调用)。
7. **反算不重新截屏、不重新 zoom**,因此它只对"最近一次 zoom 之后画面没有变化"的情形有效;工具描述里要写明这一点(画面变了要重新 zoom)。

**"看准再点"闭环(写进工具描述与 AI 动作表):**

```text
screenshot              → 全屏粗定位(截图内像素)
zoom(x, y, factor=10)   → 放大目标附近(入参=截图内像素), 返回放大图 + zoom_meta(screen_rect/factor/img_rect)
zoom_to_screen(px, py)  → 放大图里的(px,py) → screen_x/screen_y(屏幕) + img_x/img_y(截图内像素)
click(x, y)             → 用上一步返回的 img_x/img_y 点击
```

> **坐标口径(实现时统一为一种, 见 §8)**:MCP 层所有工具(含 `zoom`/`zoom_to_screen`)一律收发"截图内像素", 屏幕坐标换算只发生在 MCP 边界;因此 `zoom_to_screen` **同时**返回 `screen_x/screen_y`(屏幕物理)与 `img_x/img_y`(截图内像素, 可直接交给 `click`/`move`)。`computer_core/observe.py` 内部仍按屏幕物理坐标工作。

### 2.4 UIA 文本观察通道(`computer_core/uia.py`)

**目标**:把"屏幕是一张图"变成"屏幕是一份带坐标的窗口/控件清单"。文本观察比截图**精确**(按元素而非猜坐标)且**省 token**。

**能力边界(必须写进工具描述)**:UIA 只对支持无障碍接口的程序有效(Win32 / WPF / WinForms / UWP / 大体上 Electron)。**游戏、Canvas、自绘 UI 读出来是空的**,这类场景仍只能靠截图。

**本次实现范围(已完成实测验证的部分)**:窗口级观察。

- 接口:`UIA()` 会话对象;`list_windows()` 返回列表;`element_from_hwnd(hwnd)`;`describe(elem)`。
- 数据来源:标准库 `user32.EnumWindows` 枚举顶层窗口 → `IUIAutomation::ElementFromHandle` 取元素 → 读属性。
- 每条记录字段:`hwnd`、`name`、`class`、`automation_id`、`control_type`、`control_type_name`、`rect`、`win32_title`、`visible`。
- 输出为**紧凑文本**(不是截图),供模型直接读。

**未实现(不得在本计划里假装完成)**:控件级树遍历。见 §5。

---

## 3. 实测事实与踩坑记录

> 全部实测于 2026-09-11,Python 3.14.3,Windows,屏幕 1920×1080。探测脚本留在 `.tmp/`(见 §7.3)。

### 3.1 已验证可用的 UIA 事实(可直接采信)

```
CoInitialize(None)                                    -> hr = 0
CoCreateInstance(CLSID_CUIAutomation = FF48DBA4-60EF-4201-AA87-54103EEF594E,
                 IID_IUIAutomation   = 30CBE57D-D9D0-452A-AB13-7AC5AC4825EE,
                 CLSCTX_INPROC_SERVER = 1)            -> hr = 0
IUIAutomation::GetRootElement                         -> hr = 0
   controlType=50033(Pane) name='桌面 1' class='#32769'
IUIAutomation::ElementFromHandle(GetForegroundWindow()) -> hr = 0
```

**已验证的 vtable 索引表(这是本计划最值钱的部分):**

| 接口 | vtable 索引 | 方法 | 实测 |
|---|---|---|---|
| `IUIAutomation` | 5 | `GetRootElement` | hr=0,返回桌面根元素 |
| `IUIAutomation` | 6 | `ElementFromHandle(hwnd, &elem)` | hr=0,8/8 窗口成功 |
| `IUIAutomation` | 15 | (无参工厂,返回非空对象) | hr=0,**但用途存疑**,见 §3.7 |
| `IUIAutomationElement` | 21 | `get_CurrentControlType` → int | hr=0 |
| `IUIAutomationElement` | 23 | `get_CurrentName` → BSTR | hr=0 |
| `IUIAutomationElement` | 29 | `get_CurrentAutomationId` → BSTR | hr=0 |
| `IUIAutomationElement` | 30 | `get_CurrentClassName` → BSTR | hr=0 |
| `IUIAutomationElement` | 43 | `get_CurrentBoundingRectangle` → RECT | hr=0,根元素返回 (0,0,1920,1080) |

**已验证的窗口级观察输出样例**(真实运行结果):

```
[1] type=50032 rect=(1898,1018,1920,1040) class='CToastContainer'      name='Radmin VPN'
[2] type=50032 rect=(-8,-8,1928,1048)     class='Chrome_WidgetWin_1'    name='... - 夸克'
[3] type=50032 rect=(350,177,1479,812)    class='CASCADIA_HOSTING_WINDOW_CLASS' name='Windows PowerShell'
[4] type=50032 rect=(411,162,1645,905)    class='CabinetWClass'         name='小说'
[5] type=50032 rect=(229,258,1023,868)    class='EVERYTHING'            name='Everything'
[6] type=50032 rect=(800,296,1120,744)    class='Chrome_WidgetWin_1'    name='QQ'
```

### 3.2 坑 1:pywin32 的 ProgID 路线是死路(别浪费时间)

```python
import win32com.client
uia = win32com.client.Dispatch("UIAutomationClient.CUIAutomation")
# pywintypes.com_error: (-2147221005, '无效的类字符串', None, None)
```

原因:UIA 的 `CUIAutomation` 不注册 ProgID,也不实现 `IDispatch`,无法用 `Dispatch` 后期绑定。**结论:直接 `CLSID` + `IID` + vtable,不要碰 win32com。**(本机 `H:\PY` 里 pywin32 确实可用,但与本任务无关。)

### 3.3 坑 2:HRESULT 会自动抛异常,调试时看不到 hr 值

```python
fn = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(vtable[6])
fn(p, byref(out))   # hr<0 时抛 OSError: [WinError -2147467259] 未指定的错误
```

`ctypes` 对 `restype=HRESULT` **自动检查并抛 `OSError`**,不返回负值。排查时要把每次调用包在 `try/except OSError` 里,否则只能看到一句"未指定的错误"。§3.1 表格里的 `hr=0` 都是在成功路径上打印的。

### 3.4 坑 3:必须先 CoInitialize

不调用 `ole32.CoInitialize(None)` 时:

```
OSError: [WinError -2147221008] 尚未调用 CoInitialize。
```

`CoInitialize` 的参数是 `LPVOID`(传 `None`),返回 0(S_OK)或 1(S_FALSE,表示本线程已初始化过)。把 `UIA` 对象限制在**创建它的线程内使用**(COM STA 规则)。

### 3.5 坑 4:读 BSTR 不能用 `POINTER(c_wchar_p)`

```python
nm = ctypes.c_wchar_p()
fn(ev, 23, ctypes.POINTER(ctypes.c_wchar_p))(elem, ctypes.byref(nm))
print(nm.value)   # AttributeError: 'str' object has no attribute 'value'
```

正确写法——用 `c_void_p` 接收 BSTR 指针,再 `wstring_at`:

```python
v = ctypes.c_void_p()
fn(ev, 23, ctypes.POINTER(ctypes.c_void_p))(elem, ctypes.byref(v))
name = ctypes.wstring_at(v.value) if v.value else ""
```

> 严谨实现应再调 `oleaut32.SysFreeString(v)` 释放 BSTR。本计划先不做,但要在代码注释里标明这是已知的轻微泄漏。

### 3.6 坑 5:vtable 索引不能盲扫(会 access violation)

实测扫描 `IUIAutomation` 索引 11/12/13 和 41~46 时:

```
idx=41 hr=0 ptr=0x0                                   # 返回空
idx=42 exception: access violation reading 0xFFFF...  # 崩溃
idx=43 exception: access violation reading 0x01       # 崩溃
idx=11 exception: access violation reading 0xFFFF...
idx=12 exception: access violation reading 0x28
```

原因:x64 调用约定下"多传参数"通常无害(落在 shadow space),但**"少传参数"会让被调用者把垃圾寄存器/栈值当指针解引用而崩溃**。**结论:索引只能按头文件顺序推算 + 谨慎验证,不能靠暴力扫描。**

### 3.7 坑 6:`FindAll` 未打通(控件级遍历仍是缺口)

```python
# element vtable idx=6 是 FindAll(scope, condition, &array)
hr = FindAll(elem, 4, true_condition, byref(arr))
# -> OSError: [WinError -2147467259] 未指定的错误 (E_FAIL)
```

已排除的变量:`scope=2(Children)` 与 `scope=4(Descendants)` 都失败;条件对象来自 `IUIAutomation` idx=15 的工厂(返回非空指针)。`IUIAutomationElementArray::get_Length`(idx=3)/`GetElement`(idx=4) 尚未验证。

**下一步排查方向:** 确认 `CreateTrueCondition` 的真实索引(`IUIAutomation` 头文件顺序推算为 15,但 idx=11/12/13 的崩溃说明该段索引与推算有偏差)→ 改用 `get_ControlViewCondition` 的正确索引取条件 → 或改用 `FindFirst`(idx=5) 逐步下探。

### 3.8 坑 7:pynput 侧键——好消息,推翻预期

```
Button 枚举: ['unknown', 'left', 'middle', 'right', 'x1', 'x2']
```

pynput 1.8.2 **原生支持侧键 `x1`/`x2`**,不需要 Win32 `SendInput`。唯一无法绕过的:部分鼠标厂商驱动把侧键硬映射为"前进/后退",那是驱动层行为。

### 3.9 坑 8:DSH 沙箱下 `vendor` 不可枚举(环境影响自测)

在 DSH `workspace-write` 沙箱内:

```
Get-ChildItem H:\cu-a\vendor\pynput   -> [sandbox] denied: true
python -c "from input_engine import mouse"
    -> ModuleNotFoundError: No module named 'pynput.mouse'
```

即使 `vendor\pynput\mouse\__init__.py` 文件存在且可读,**目录不可枚举**导致 `pynput.mouse` 导入失败。**你项目自己的 `input_engine/mouse.py` 同样导入失败**。提权到 `danger-full-access` 后:

```
pynput 导入 OK: ['unknown', 'left', 'middle', 'right', 'x1', 'x2']
```

**结论:在 DSH 里跑本项目代码做自测,每次都需要一次提权;你在沙箱外直接 `python main.py` 不受影响。**

### 3.10 设计坑(尚未踩,但会踩)

1. **放大镜必须 `Image.NEAREST`**。10X 用 LANCZOS 会把 1px 细线糊成渐变,模型的"显微镜"就废了。
2. **倍率与视野成反比**。10X + 输出上限 1200px ⇒ 只覆盖屏幕 120×120。必须支持模型指定坐标,做"粗看→精看"两级流程。
3. **坐标映射必须回传**。放大图像素 ≠ 屏幕像素,不回传 `screen_rect` 和 `factor` 的话,模型必然点错。
4. **`hold_ms` 是阻塞调用**。`press → sleep → release` 卡住线程,必须有 `HOLD_MS_MAX = 5000` 上限,否则模型传 999999 就把 AI 任务冻死。
5. **双击间隔**。真双击要求两次 down/up 间隔 < `GetDoubleClickTime()`(默认 500ms)。现有 `for _ in range(clicks): mc.click(...)` 间隔取决于执行速度、不可控。
6. **反算是"最近一次 zoom"的状态,不是纯数学**。放大图与屏幕的映射每次都不一样;反算接口必须绑定最近一次的 meta,并在没有记录时**明确报错**,否则模型会拿旧的 factor 算出一个偏得很远的坐标还以为成功。超界像素同样要报错而不是夹取。
7. **`gap_ms` 与 `interval_ms` 绝不能合并**。序列的"点与点之间"和双击的"两次 down/up 之间"语义不同,合成一个参数后"点一下 → 等 300ms → 点下一个"与"双击"就会互相污染。
8. **序列的中间失败**。序列执行到第 k 个点时若抛异常(例如坐标非法),已经执行的 k-1 个点不回滚;错误信息必须带上"已完成 k-1 个,第 k 个参数是 ...",否则模型无法判断鼠标现在停在哪。

---

## 4. 任务分解

> ✅ 5 个 Task 已于 2026-09-11 全部实施并通过验收(41 个单测全绿);提交链、落地偏差与验证结果见 §6 / §8。

> 项目当前**没有测试基础设施**。为避免新增依赖,测试统一用标准库 `unittest`,放 `tests/`,用 `python -m unittest` 运行。只对**纯函数**(坐标换算、参数校验、映射表)写单元测试;序列的 `sleep` 节奏用 `mock` 断言调用次数与参数(不真的等待);真实鼠标/键盘行为用人工验证步骤。

### Task 1: 输入层原语(移动与点击解耦 + 执行时间 + 坐标序列)

**Files:**
- Modify: `input_engine/mouse.py`
- Test: `tests/test_mouse_input.py` (新建)

**Interfaces:**
- Consumes: 现有 `mc = MouseController()`、`move_to()`、`move_absolute()`
- Produces:
  - `BUTTONS: dict[str, pynput.mouse.Button]`
  - `HOLD_MS_MAX: int = 5000`
  - `DOUBLE_CLICK_INTERVAL_MS: int = 80`
  - `GAP_MS_DEFAULT: int = 0`
  - `normalize_points(points, default_t) -> list[tuple[float, float, float]]`(纯函数, 序列校验/解包)
  - `move(x=None, y=None, mode="smooth", duration_ms=None, points=None, gap_ms=0, cfg=None) -> None`
  - `click(button="left", hold_ms=0, clicks=1, interval_ms=None, at=None, points=None, gap_ms=0, cfg=None) -> None`

- [ ] **Step 1: 写失败测试**(纯函数部分,不碰真实鼠标)

```python
# tests/test_mouse_input.py
import sys, os, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))

from input_engine import mouse


class TestButtonMap(unittest.TestCase):
    def test_side_buttons_present(self):
        # 实测 pynput 1.8.2 支持 x1/x2
        for name in ("left", "right", "middle", "x1", "x2"):
            self.assertIn(name, mouse.BUTTONS)

    def test_hold_ms_upper_bound(self):
        self.assertEqual(mouse.HOLD_MS_MAX, 5000)

    def test_unknown_button_raises(self):
        with self.assertRaises(ValueError):
            mouse.click(button="nope")

    def test_hold_ms_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            mouse.click(button="left", hold_ms=mouse.HOLD_MS_MAX + 1)
        with self.assertRaises(ValueError):
            mouse.click(button="left", hold_ms=-1)


class TestMoveClickAreIndependent(unittest.TestCase):
    """移动与点击彻底独立: move 不点击, click 不带坐标不移动。"""

    def test_move_never_clicks(self):
        with mock.patch.object(mouse, "mc") as m, mock.patch.object(mouse, "move_to") as mt:
            mouse.move(10, 20)
            self.assertEqual(m.click.call_count, 0)
            mt.assert_called_once()

    def test_bare_click_does_not_move(self):
        with mock.patch.object(mouse, "mc") as m, mock.patch.object(mouse, "move_to") as mt:
            mouse.click(button="left")
            m.click.assert_called_once()
            mt.assert_not_called()          # 桩 mc 不会被真的设位置, 只看没有移动调用

    def test_click_at_positioning_keeps_legacy_instant_behavior(self):
        with mock.patch.object(mouse, "mc") as m, mock.patch.object(mouse, "move_to") as mt:
            mouse.click(at=(100, 200))
            mt.assert_not_called()               # 旧行为是瞬移, 不是平滑移动
            self.assertEqual(m.click.call_count, 1)


class TestPointSequenceValidation(unittest.TestCase):
    def test_points_and_xy_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            mouse.move(100, 200, points=[[1, 2]])
        with self.assertRaises(ValueError):
            mouse.click(x=100, y=200, points=[[1, 2]])
        with self.assertRaises(ValueError):
            mouse.click(at=(1, 2), points=[[1, 2]])

    def test_empty_points_raises(self):
        for f in (lambda: mouse.move(points=[]), lambda: mouse.click(points=[])):
            with self.assertRaises(ValueError):
                f()

    def test_bad_point_shape_or_type_raises(self):
        for bad in ([[1]], [[1, 2, 3, 4]], [["a", "b"]], [None], "1,2"):
            with self.assertRaises(ValueError):
                mouse.move(points=bad)

    def test_missing_target_raises(self):
        with self.assertRaises(ValueError):
            mouse.move()                          # 既没有 x/y 也没有 points

    def test_gap_ms_negative_raises(self):
        with self.assertRaises(ValueError):
            mouse.move(points=[[1, 2]], gap_ms=-1)
        with self.assertRaises(ValueError):
            mouse.click(points=[[1, 2]], gap_ms=-1)

    def test_per_point_time_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            mouse.click(points=[[1, 2, mouse.HOLD_MS_MAX + 1]])
        with self.assertRaises(ValueError):
            mouse.move(points=[[1, 2, -50]])

    def test_normalize_points_defaults(self):
        self.assertEqual(mouse.normalize_points([[1, 2], [3, 4, 500]], 300),
                         [(1.0, 2.0, 300.0), (3.0, 4.0, 500.0)])


class TestSequenceExecution(unittest.TestCase):
    """用桩替换 mc / time.sleep, 验证序列按顺序走完且只在点与点之间等待。"""

    def test_click_sequence_order_and_single_gap(self):
        with mock.patch.object(mouse, "mc") as m, mock.patch.object(mouse.time, "sleep") as s:
            mouse.click(points=[[10, 20], [30, 40], [50, 60]], gap_ms=200)
        self.assertEqual(m.click.call_count, 3)
        self.assertEqual(s.call_count, 2)                       # 3 个点 -> 2 个间歇
        self.assertEqual(s.call_args_list[0].args[0], 0.2)

    def test_move_sequence_uses_per_point_duration(self):
        seen = []
        with mock.patch.object(mouse, "move_to",
                               side_effect=lambda x, y, cfg, d: seen.append(d)), \
             mock.patch.object(mouse.time, "sleep"):
            mouse.move(points=[[1, 1, 111], [2, 2]], duration_ms=222, gap_ms=50)
        self.assertEqual(seen, [111, 222])                      # 三元覆盖, 两元用函数级值

    def test_single_point_path_unchanged(self):
        with mock.patch.object(mouse, "move_to") as mt:
            mouse.move(7, 8, duration_ms=99)
            mt.assert_called_once_with(7, 8, None, 99)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_mouse_input -v`
Expected: FAIL — `AttributeError: module 'input_engine.mouse' has no attribute 'BUTTONS'`

- [ ] **Step 3: 实现**

在 `input_engine/mouse.py` 中,保留全部现有函数,新增:

```python
BUTTONS = {
    "left": Button.left,
    "right": Button.right,
    "middle": Button.middle,
    "x1": Button.x1,
    "x2": Button.x2,
}

HOLD_MS_MAX = 5000                 # 单次按压上限
DOUBLE_CLICK_INTERVAL_MS = 80      # 同一次点击内部多次 down/up 的间隔
GAP_MS_DEFAULT = 0                 # 序列相邻两点之间的间歇


def normalize_points(points, default_t: float) -> list[tuple[float, float, float]]:
    """把 [[x,y], [x,y,t], ...] 规范成 [(x, y, t), ...]; 非法输入抛 ValueError。"""
    if not isinstance(points, (list, tuple)) or len(points) == 0:
        raise ValueError("points 必须是非空列表, 元素形如 [x, y] 或 [x, y, t]")
    out = []
    for i, p in enumerate(points):
        if not isinstance(p, (list, tuple)) or len(p) not in (2, 3):
            raise ValueError(f"points[{i}] 必须是 [x, y] 或 [x, y, t], 收到 {p!r}")
        try:
            px, py = float(p[0]), float(p[1])
            pt = float(p[2]) if len(p) == 3 else float(default_t)
        except (TypeError, ValueError):
            raise ValueError(f"points[{i}] 含非数字: {p!r}")
        out.append((px, py, pt))
    return out


def move(x: float | None = None, y: float | None = None, mode: str = "smooth",
         duration_ms: float | None = None, points: list | None = None,
         gap_ms: float = GAP_MS_DEFAULT, cfg: dict | None = None) -> None:
    """只移动, 不点击。mode='smooth' 插值+抖动+加速; 'instant' 瞬移。
    单点: move(100, 200, duration_ms=500)
    序列: move(points=[[100, 200], [300, 400, 300]], gap_ms=120)
    """
    if mode not in ("smooth", "instant"):
        raise ValueError(f"未知 move mode: {mode} (可选 smooth/instant)")
    if gap_ms < 0:
        raise ValueError(f"gap_ms 必须 >= 0, 收到 {gap_ms}")

    if points is not None:
        if x is not None or y is not None:
            raise ValueError("points 与 x/y 互斥, 只能给一个")
        seq = normalize_points(points, duration_ms if duration_ms is not None else 0)
        for i, (px, py, pt) in enumerate(seq):
            if pt < 0:
                raise ValueError(f"points[{i}] 的时间必须 >= 0, 收到 {pt}")
            move(px, py, mode=mode, duration_ms=(pt or None), cfg=cfg)
            if i < len(seq) - 1 and gap_ms:
                time.sleep(gap_ms / 1000.0)
        return

    if x is None or y is None:
        raise ValueError("move 需要 x/y(单点) 或 points(序列) 其中之一")

    if mode == "instant":
        mc.position = (x, y)
        return
    move_to(x, y, cfg, duration_ms)


def click(button: str = "left", hold_ms: float = 0, clicks: int = 1,
          interval_ms: float | None = None, at: tuple | None = None,
          points: list | None = None, gap_ms: float = GAP_MS_DEFAULT,
          cfg: dict | None = None) -> None:
    """只点击(除非给了 at / points)。hold_ms 为按压时长(毫秒), 上限 HOLD_MS_MAX。
    单点: click(x=100, y=200, hold_ms=120)
    序列: click(points=[[100, 200], [300, 400, 60]], button="left", gap_ms=200)
    注意: interval_ms 是双击间隔, gap_ms 是序列步间间隔, 不要混用。
    """
    btn = BUTTONS.get(button)
    if btn is None:
        raise ValueError(f"未知按键: {button} (可选 {sorted(BUTTONS)})")
    if hold_ms < 0 or hold_ms > HOLD_MS_MAX:
        raise ValueError(f"hold_ms 必须在 0~{HOLD_MS_MAX} 之间, 收到 {hold_ms}")
    if clicks < 1:
        raise ValueError(f"clicks 必须 >= 1, 收到 {clicks}")
    if gap_ms < 0:
        raise ValueError(f"gap_ms 必须 >= 0, 收到 {gap_ms}")

    if points is not None:
        if at is not None:
            raise ValueError("points 与 at 互斥, 只能给一个")
        seq = normalize_points(points, hold_ms)
        for i, (px, py, ph) in enumerate(seq):
            if ph < 0 or ph > HOLD_MS_MAX:
                raise ValueError(f"points[{i}] 的按压时长必须在 0~{HOLD_MS_MAX} 之间, 收到 {ph}")
            click(button=button, hold_ms=ph, clicks=clicks,
                  interval_ms=interval_ms, at=(px, py), cfg=cfg)
            if i < len(seq) - 1 and gap_ms:
                time.sleep(gap_ms / 1000.0)
        return

    if interval_ms is None:
        interval_ms = DOUBLE_CLICK_INTERVAL_MS

    if at is not None:
        mc.position = (at[0], at[1])   # 点击前定位保持旧行为(瞬移)

    for i in range(clicks):
        if hold_ms > 0:
            mc.press(btn)
            time.sleep(hold_ms / 1000.0)
            mc.release(btn)
        else:
            mc.click(btn, 1)
        if i < clicks - 1:
            time.sleep(interval_ms / 1000.0)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_mouse_input -v`
Expected: PASS (18 tests) —— 4 个原有 + 移动/点击独立性 4(含旧位置参数兼容) + 序列校验 7 + 序列执行节奏 3

> 沙箱提示:此步需要 DSH 提权到 `danger-full-access`(见 §3.9)。

- [ ] **Step 5: 人工验证侧键与长按**(需要手动执行,鼠标会真的动)

```bash
python -c "import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']; from input_engine import mouse; import time; time.sleep(2); mouse.click(button='x1'); print('侧键 x1 已发送')"
```
Expected: 浏览器/资源管理器中触发"后退"。

再验证"执行时间 + 序列 + 步间间隔"(先在屏幕上打开画图/白板,让它画出 Z 字形):

```bash
python -c "import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']; from input_engine import mouse; import time; time.sleep(2); mouse.move(points=[[400,300],[800,300],[400,600],[800,600]], duration_ms=400, gap_ms=300); mouse.click(points=[[400,700],[800,700]], hold_ms=150, gap_ms=500); print('OK')"
```
Expected: 鼠标平滑走过 4 个点(每段约 400ms,段间停 300ms),再在两个位置各长按 150ms 点击一次。

- [ ] **Step 6: 提交**

```bash
git add input_engine/mouse.py tests/test_mouse_input.py
git commit -m "重构:鼠标输入层移动与点击彻底独立, 支持按压时长/侧键/双击间隔与带步间间隔的坐标序列"
```

### Task 2: 服务层适配与向后兼容

**Files:**
- Modify: `computer_core/service.py` (`click` 方法,约 105 行附近)
- Test: `tests/test_service_click.py` (新建)

**Interfaces:**
- Consumes: Task 1 的 `mouse.move()` / `mouse.click()`
- Produces:
  - `ComputerService.move(x=None, y=None, mode="smooth", duration_ms=None, points=None, gap_ms=0) -> dict`
  - `ComputerService.click(x=None, y=None, button="left", clicks=1, hold_ms=0, interval_ms=None, move_mode="instant", points=None, gap_ms=0) -> dict`
  - 两者的返回值都是执行记录 `{"count": n, "points": [[sx, sy], ...], "gap_ms": g, "duration_ms"/"hold_ms": t}`

- [ ] **Step 1: 写失败测试**(用桩替换真实鼠标,验证参数转发)

```python
# tests/test_service_click.py
import sys, os, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))

from computer_core import service


class TestServiceClick(unittest.TestCase):
    def _svc(self):
        return service.ComputerService(config={"ai": {}, "draw": {}})

    def test_click_forwards_hold_and_button(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "click") as m:
            svc.click(10, 20, button="x1", hold_ms=300, clicks=2)
            m.assert_called_once()
            kw = m.call_args.kwargs
            self.assertEqual(kw["button"], "x1")
            self.assertEqual(kw["hold_ms"], 300)
            self.assertEqual(kw["clicks"], 2)
            self.assertEqual(kw["at"], (10, 20))

    def test_click_without_coords_does_not_move(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "click") as m:
            svc.click(button="left")
            self.assertIsNone(m.call_args.kwargs["at"])


class TestServiceMoveAndSequence(unittest.TestCase):
    def _svc(self):
        return service.ComputerService(config={"ai": {}, "draw": {}})

    def test_move_forwards_duration(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "move") as m:
            rec = svc.move(10, 20, mode="smooth", duration_ms=500)
            m.assert_called_once()
            self.assertEqual(m.call_args.kwargs["duration_ms"], 500)
            self.assertEqual(rec["count"], 1)

    def test_move_sequence_forwards_points_and_gap(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "move") as m:
            rec = svc.move(points=[[1, 2], [3, 4]], gap_ms=150)
            self.assertEqual(m.call_args.kwargs["points"], [[1, 2], [3, 4]])
            self.assertEqual(m.call_args.kwargs["gap_ms"], 150)
            self.assertEqual(rec["gap_ms"], 150)

    def test_click_sequence_returns_record(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "click") as m:
            rec = svc.click(points=[[5, 6], [7, 8]], hold_ms=120, gap_ms=200)
            self.assertEqual(m.call_args.kwargs["points"], [[5, 6], [7, 8]])
            self.assertEqual(m.call_args.kwargs["hold_ms"], 120)
            self.assertEqual(rec["count"], 2)

    def test_click_without_coords_still_does_not_move(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "click") as m:
            svc.click(button="left")
            self.assertIsNone(m.call_args.kwargs["at"])
            self.assertIsNone(m.call_args.kwargs["points"])

    def test_click_smooth_mode_moves_before_clicking(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "move") as mm, \
             mock.patch.object(service.mouse, "click") as mc:
            svc.click(10, 20, move_mode="smooth")
            mm.assert_called_once()
            self.assertIsNone(mc.call_args.kwargs["at"])   # 已单独移动, 点击不再定位


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_service_click -v`
Expected: FAIL — `TypeError: click() got an unexpected keyword argument 'hold_ms'`

- [ ] **Step 3: 实现**

```python
def move(self, x=None, y=None, mode: str = "smooth", duration_ms=None,
         points=None, gap_ms: float = 0) -> dict:
    """只移动, 不点击。单点(x/y)或序列(points)二选一, gap_ms 为序列步间间隔。"""
    mouse.move(x, y, mode=mode, duration_ms=duration_ms,
               points=points, gap_ms=gap_ms)
    pts = [[p[0], p[1]] for p in points] if points else ([[x, y]] if x is not None else [])
    log.info("move %s -> %s gap=%sms", mode, pts, gap_ms)
    return {"count": len(pts), "points": pts, "gap_ms": gap_ms,
            "duration_ms": duration_ms}


def click(self, x=None, y=None, button: str = "left", clicks: int = 1,
          hold_ms: float = 0, interval_ms: float | None = None,
          move_mode: str = "instant", points=None, gap_ms: float = 0) -> dict:
    """点击。给了 points 走序列; 给了 x/y 先定位(move_mode=instant 瞬移/smooth 平滑)再点; 都不给则原地点击。"""
    if move_mode not in ("instant", "smooth"):
        raise ValueError(f"未知 move_mode: {move_mode} (可选 instant/smooth)")

    if points is not None:
        mouse.click(button=button, hold_ms=hold_ms, clicks=clicks,
                    interval_ms=interval_ms, points=points, gap_ms=gap_ms)
        pts = [[p[0], p[1]] for p in points]
        log.info("click seq %s hold=%sms gap=%sms", pts, hold_ms, gap_ms)
        return {"count": len(pts), "points": pts, "gap_ms": gap_ms,
                "hold_ms": hold_ms, "button": button}

    at = None
    if x is not None and y is not None:
        if move_mode == "smooth":
            mouse.move(x, y, mode="smooth")     # 移动与点击独立: 平滑段单独调用
        else:
            at = (x, y)                          # 旧行为: 点击内部瞬移
    mouse.click(button=button, hold_ms=hold_ms, clicks=clicks,
                interval_ms=interval_ms, at=at)
    log.info("click %s hold=%sms clicks=%d at=%s", button, hold_ms, clicks, at)
    pts = [[x, y]] if at else []
    return {"count": len(pts), "points": pts, "gap_ms": gap_ms,
            "hold_ms": hold_ms, "button": button}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_service_click -v`
Expected: PASS (7 tests) —— 2 个旧用例(参数转发/不带坐标不移动) + 5 个新用例(独立 move / 序列转发 / 序列记录 / 原地点击不变 / smooth 先移动)

- [ ] **Step 5: 回归验证旧调用路径**

Run: `python -c "import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']; from computer_core.service import ComputerService; s=ComputerService(); print(s.get_state())"`
Expected: 打印 `{'mode': 'work', 'cursor': [...], 'brush_size': 1, 'pen_channel': False}`,无异常。

- [ ] **Step 6: 提交**

```bash
git add computer_core/service.py tests/test_service_click.py
git commit -m "重构:服务层点击支持按压时长/侧键/双击, 新增独立 move 接口"
```

### Task 3: 放大镜观察工具 + 像素反算

**Files:**
- Create: `computer_core/observe.py`
- Test: `tests/test_observe_zoom.py` (新建)

**Interfaces:**
- Produces:
  - `zoom(x=None, y=None, factor=10, src=100, quality=85, max_out=1200) -> (bytes, dict)`(成功即记住本次映射)
  - `clamp_region(cx, cy, src, screen_size) -> tuple`;`effective_factor(src, factor, max_out) -> int`(纯函数)
  - `px_to_screen(px, py, meta=None) -> tuple[int, int]`(**反算**,meta 省略用最近一次 zoom)
  - `screen_to_px(sx, sy, meta) -> tuple[int, int]`(正向,用于往返自检)
  - `last_meta() -> dict | None`;`_LAST`(最近一次 `{"meta", "img"}`);`_LOCK: threading.Lock`

- [ ] **Step 1: 写失败测试**(只测纯函数)

```python
# tests/test_observe_zoom.py
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))

from computer_core import observe


class TestClampRegion(unittest.TestCase):
    def test_center_region_no_clamp(self):
        self.assertEqual(observe.clamp_region(500, 500, 100, (1920, 1080)),
                         (450, 450, 550, 550))

    def test_left_top_clamped(self):
        # 中心点靠近左上角时必须夹到 (0,0)
        self.assertEqual(observe.clamp_region(10, 10, 100, (1920, 1080)),
                         (0, 0, 100, 100))

    def test_right_bottom_clamped(self):
        self.assertEqual(observe.clamp_region(1915, 1075, 100, (1920, 1080)),
                         (1820, 980, 1920, 1080))


class TestZoomFactorBudget(unittest.TestCase):
    def test_effective_factor_kept(self):
        self.assertEqual(observe.effective_factor(100, 10, 1200), 10)

    def test_effective_factor_reduced(self):
        self.assertEqual(observe.effective_factor(500, 10, 1200), 2)


class TestPxToScreen(unittest.TestCase):
    """反算: 放大图像素 -> 屏幕真实坐标(计划 §2.3.1)。"""

    META = {"screen_rect": [450, 450, 550, 550], "factor": 10,
            "src_size": [100, 100], "out_size": [1000, 1000], "seq": 1}

    def test_origin_maps_to_rect_topleft(self):
        self.assertEqual(observe.px_to_screen(0, 0, self.META), (450, 450))

    def test_integer_division_by_factor(self):
        # factor×factor 个放大像素 = 屏幕 1 像素
        self.assertEqual(observe.px_to_screen(19, 20, self.META), (451, 452))

    def test_last_pixel_stays_inside_rect(self):
        self.assertEqual(observe.px_to_screen(999, 999, self.META), (549, 549))

    def test_out_of_range_raises_with_range_in_message(self):
        with self.assertRaises(ValueError) as ctx:
            observe.px_to_screen(1000, 0, self.META)   # px == out_w 已越界
        self.assertIn("1000x1000", str(ctx.exception))
        with self.assertRaises(ValueError):
            observe.px_to_screen(0, -1, self.META)

    def test_non_numeric_raises(self):
        with self.assertRaises(ValueError):
            observe.px_to_screen("a", 0, self.META)

    def test_no_zoom_record_raises(self):
        observe._LAST = None
        with self.assertRaises(RuntimeError):
            observe.px_to_screen(0, 0, None)          # 没传 meta 且没有记录

    def test_roundtrip_screen_to_px_to_screen(self):
        for sx, sy in ((450, 450), (455, 462), (549, 549)):
            px, py = observe.screen_to_px(sx, sy, self.META)
            self.assertEqual(observe.px_to_screen(px, py, self.META), (sx, sy))

    def test_remember_records_meta_and_increments_seq(self):
        observe._LAST = None
        observe._seq_counter = 0
        observe._remember({"out_size": [10, 10], "factor": 1,
                           "screen_rect": [0, 0, 10, 10]}, None)
        observe._remember({"out_size": [10, 10], "factor": 1,
                           "screen_rect": [0, 0, 10, 10]}, None)
        self.assertEqual(observe.last_meta()["seq"], 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_observe_zoom -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'computer_core.observe'`

- [ ] **Step 3: 实现**

```python
"""观察通道: 放大镜(zoom) + 坐标反算(px_to_screen)。坐标系: 屏幕物理像素。"""
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
    """放大 (x,y) 周围 src×src 区域。x/y 为 None 时用当前鼠标位置。
    返回 (jpeg_bytes, meta)。meta.screen_rect / meta.factor 是模型换算坐标的依据。
    """
    if x is None or y is None:
        x, y = mouse.position()
    x, y = int(x), int(y)

    img0 = ImageGrab.grab(all_screens=True)
    left, top, right, bottom = clamp_region(x, y, src, img0.size)
    region = img0.crop((left, top, right, bottom))

    f = effective_factor(region.width, factor, max_out)
    big = region.resize((region.width * f, region.height * f), Image.NEAREST)

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
    _remember(meta, big)          # 记住最近一次映射, 供 px_to_screen 反算(见 §2.3.1)
    return buf.getvalue(), meta


# ---- 反算: 放大图像素 -> 屏幕真实坐标(§2.3.1) ----

_LAST = None                              # {"meta": dict, "img": PIL.Image} | None
_seq_counter = 0                          # 观察序号, 回带给模型核对"看的是哪张图"
_LOCK = threading.Lock()


def _remember(meta: dict, img) -> dict:
    """记下最近一次成功观察的映射(zoom 内部调用, 反算依赖它)。"""
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_observe_zoom -v`
Expected: PASS (13 tests) —— 夹取 3 + 倍率预算 2 + 反算 8(含超界/无记录/往返一致/seq 自增)

- [ ] **Step 5: 真实截图人工验证**

```bash
python -c "import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']; from computer_core.observe import zoom; b,m=zoom(factor=10); open(r'H:\cu-a\.tmp\zoom_check.jpg','wb').write(b); print(m)"
```
Expected: 生成 `.tmp/zoom_check.jpg`,meta 里 `factor=10`、`screen_rect` 为 100×100 的屏幕矩形、`seq=1`。打开图片确认**无插值模糊**(细线边缘是硬边)。

再验证反算(不传 meta,走"最近一次 zoom"):

```bash
python -c "import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']; from computer_core.observe import zoom, px_to_screen, screen_to_px; b,m=zoom(factor=10); print('seq', m['seq']); print('图中心 ->', px_to_screen(m['out_size'][0]//2, m['out_size'][1]//2)); print('往返', px_to_screen(*screen_to_px(m['cursor'][0], m['cursor'][1], m))); print('应等于', tuple(m['cursor']))"
```
Expected: 图中心反算结果落在 `screen_rect` 中心附近;往返结果**精确等于**鼠标当前位置(证明正反变换互逆)。

- [ ] **Step 6: 提交**

```bash
git add computer_core/observe.py tests/test_observe_zoom.py
git commit -m "新增:10X 鼠标放大镜观察工具(NEAREST 插值+坐标映射回传)与放大图像素->屏幕坐标反算"
```

### Task 4: UIA 窗口级观察模块

**Files:**
- Create: `computer_core/uia.py`
- Test: `tests/test_uia_helpers.py` (新建)

**Interfaces:**
- Produces: `UIA()` 会话类;`UIA.list_windows() -> list[dict]`;`UIA.describe(elem) -> dict`;`UIA.format_windows(max_items=30) -> str`;`CONTROL_TYPES: dict[int,str]`

- [ ] **Step 1: 写失败测试**(纯函数/常量,不碰 COM)

```python
# tests/test_uia_helpers.py
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))

from computer_core import uia


class TestConstants(unittest.TestCase):
    def test_clsid_and_iid_are_fixed(self):
        self.assertEqual(uia.CLSID_CUIAutomation, "{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
        self.assertEqual(uia.IID_IUIAutomation, "{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")

    def test_verified_vtable_indices_are_locked(self):
        # 这些索引是实测结论, 改动必须重新验证
        self.assertEqual(uia.UIA._IDX_GET_ROOT, 5)
        self.assertEqual(uia.UIA._IDX_FROM_HWND, 6)
        self.assertEqual(uia.UIA._IDX_CONTROL_TYPE, 21)
        self.assertEqual(uia.UIA._IDX_NAME, 23)
        self.assertEqual(uia.UIA._IDX_AUTOMATION_ID, 29)
        self.assertEqual(uia.UIA._IDX_CLASS, 30)
        self.assertEqual(uia.UIA._IDX_RECT, 43)

    def test_known_control_types(self):
        self.assertEqual(uia.CONTROL_TYPES[50032], "Window")
        self.assertEqual(uia.CONTROL_TYPES[50033], "Pane")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_uia_helpers -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'computer_core.uia'`

- [ ] **Step 3: 实现**(索引全部来自 §3.1 实测)

```python
"""UIA 文本观察通道(纯 ctypes, 零第三方依赖)。

实测环境: Python 3.14.3 / Windows / 1920x1080, 2026-09-11。

已验证:
  - CoInitialize + CoCreateInstance(CLSID_CUIAutomation)
  - IUIAutomation         : GetRootElement / ElementFromHandle
  - IUIAutomationElement  : ControlType / Name / AutomationId / ClassName / BoundingRectangle
  - EnumWindows + ElementFromHandle 窗口级观察

未打通(见计划文档 §5):
  - 控件级遍历(FindAll 返回 E_FAIL; TreeWalker 索引未定位)

注意: COM 为 STA, UIA 实例必须在创建它的线程内使用。
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


def _vtable(obj) -> POINTER(c_void_p):
    return ctypes.cast(ctypes.cast(obj, POINTER(c_void_p))[0], POINTER(c_void_p))


def _fn(vt, idx, *argtypes):
    """绑定 vtable 第 idx 个方法。注意: restype=HRESULT 时 ctypes 会自动抛 OSError,
    因此调用处必须 try/except 才能看到 hr。"""
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_uia_helpers -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 真实环境人工验证**

```bash
python -c "import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']; from computer_core.uia import UIA; print(UIA().format_windows())"
```
Expected: 打印当前可见窗口清单,每行含 `hwnd`、`type=Window`、`rect=(l,t,r,b)`、`class`、`name`。实测参考:1920×1080 屏上可见 8~9 个窗口。

- [ ] **Step 6: 提交**

```bash
git add computer_core/uia.py tests/test_uia_helpers.py
git commit -m "新增:UIA 窗口级文本观察通道(纯 ctypes 零依赖, 含实测 vtable 索引)"
```

### Task 5: MCP 工具暴露与 AI 动作表更新

**Files:**
- Modify: `computer_mcp/server.py` (`TOOLS` 列表 + `_run_single`)
- Modify: `ai_mode/controller.py` (`SYSTEM_PROMPT` 动作表)
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1/2/3/4 的全部产物
- Produces: MCP 工具 `move`、`zoom`、`zoom_to_screen`、`list_windows`;扩展后的 `click`(新增 `hold_ms`、`points`、`gap_ms`,`button` 增加 `x1/x2`);扩展后的 `move`(新增 `duration_ms`、`points`、`gap_ms`)

- [ ] **Step 1: 新增 MCP 工具定义**

在 `TOOLS` 列表中追加(不改动现有条目的 name/参数):

```python
{"name": "move", "description": "只移动鼠标, 不点击. 单点给 x/y(可选 duration_ms 设整段耗时); 序列给 points=[[x,y],[x,y,duration_ms],...] 并用 gap_ms 设步间间隔(毫秒, 默认0). mode=smooth 平滑/instant 瞬移.",
 "inputSchema": {"type": "object",
                 "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                "mode": {"type": "string", "enum": ["smooth", "instant"]},
                                "duration_ms": {"type": "number", "description": "单次移动耗时(毫秒), 仅 smooth 生效"},
                                "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                                           "description": "坐标序列 [[x,y], [x,y,duration_ms], ...], 与 x/y 互斥"},
                                "gap_ms": {"type": "number", "description": "序列相邻两点的间歇(毫秒), 默认 0"}}}},
{"name": "zoom", "description": "放大鼠标附近或指定坐标周围区域(默认10X, NEAREST 插值). 返回图像 + meta(screen_rect/factor/out_size/seq). 想看放大图里某点的屏幕坐标, 直接把像素交给 zoom_to_screen, 不要自己心算.",
 "inputSchema": {"type": "object",
                 "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                "factor": {"type": "integer"},
                                "src": {"type": "integer"}}}},
{"name": "zoom_to_screen", "description": "把【最近一次 zoom 图像】里的像素坐标(px,py, 图左上角为0,0)换算成屏幕真实坐标, 返回 screen_x/screen_y. 越界或还没 zoom 过会报错. 注意: 本工具不重新截图, 画面已变化时请重新 zoom.",
 "inputSchema": {"type": "object",
                 "properties": {"px": {"type": "number"}, "py": {"type": "number"}},
                 "required": ["px", "py"]}},
{"name": "list_windows", "description": "列出当前可见顶层窗口(UIA 无障碍树, 返回文本). 含 hwnd/名称/类名/矩形/控件类型.",
 "inputSchema": {"type": "object", "properties": {}}},
```

并在现有 `click` 工具的 schema 中**只增加**两个可选属性:

```python
"hold_ms": {"type": "number", "description": "按压时长(毫秒), 0~5000, 默认 0"},
"points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
           "description": "坐标序列 [[x,y], [x,y,hold_ms], ...], 与 x/y 互斥; 每个点都会点击"},
"gap_ms": {"type": "number", "description": "序列相邻两点的间歇(毫秒), 默认 0; 注意与 interval_ms(双击间隔) 含义不同"},
# button 的 enum 由 ["left","right","middle"] 扩展为 ["left","right","middle","x1","x2"]
```

- [x] **Step 2: 在 `_run_single` 中分派**(以下为实际落地版本)

```python
# click 也在此分派: 新增 hold_ms / points / gap_ms
if act == "click":
    kw = {"button": action.get("button", "left"),
          "clicks": int(action.get("clicks", 1)),
          "hold_ms": float(action.get("hold_ms", 0)),
          "gap_ms": float(action.get("gap_ms", 0))}
    if action.get("points"):
        return svc.click(points=_img_points(action["points"]), **kw)
    return svc.click(_img_x(action.get("x", 0)), _img_y(action.get("y", 0)), **kw)
elif act == "move":
    # 坐标一律"截图内像素"入参, server 侧 _img_points/_img_x 换算成屏幕坐标
    if action.get("points"):
        return svc.move(points=_img_points(action["points"]),
                        gap_ms=action.get("gap_ms", 0),
                        mode=action.get("mode", "smooth"),
                        duration_ms=action.get("duration_ms"))
    return svc.move(_img_x(action["x"]), _img_y(action["y"]),
                    mode=action.get("mode", "smooth"),
                    duration_ms=action.get("duration_ms"))
elif act == "zoom_to_screen":
    r = svc.zoom_to_screen(action["px"], action["py"])
    r["img_x"], r["img_y"] = _to_img_xy(r["screen_x"], r["screen_y"])
    r["note"] = "click/move 请用 img_x/img_y(截图内像素)"
    return r
```

> `zoom` / `list_windows` **不在** `_run_single` 里: 它们在 `handle_tool_call` 特判, 因为要返回**图像**(`_image_result`)或整段文本。`zoom` 的附带文本块是 `zoom_meta`(`seq/factor/out_size/screen_rect/img_rect/hint`);`zoom_to_screen` 则同时给 `screen_x/screen_y` 与 `img_x/img_y`。

- [ ] **Step 3: 更新 `ai_mode/controller.py` 的 `SYSTEM_PROMPT`**

在动作表中**追加**以下行(不删除任何现有行;`move`/`zoom` 两行是把原计划的两行扩展成含序列/反算的版本):

```text
{"action":"move","x":..,"y":..,"duration_ms":..,"mode":"smooth|instant"}  只移动不点击
{"action":"move","points":[[x,y],[x,y,duration_ms],...],"gap_ms":120}          依次移动到多个位置(点间停 gap_ms)
{"action":"click","points":[[x,y],[x,y,hold_ms],...],"gap_ms":300}             依次点击多个位置(点间停 gap_ms)
{"action":"zoom","x":..,"y":..,"factor":10}              放大观察目标附近(10X, 只覆盖约120x120)
{"action":"zoom_to_screen","px":..,"py":..}              把上一张放大图里的像素换成屏幕坐标; 拿到结果后用 click 传屏幕坐标点击
```

- [ ] **Step 4: 冒烟测试 MCP server**

```bash
python -c "
import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']
import computer_mcp.server as s
names=[t['name'] for t in s.TOOLS]
print('工具数:', len(names)); print(names)
assert {'move', 'zoom', 'zoom_to_screen', 'list_windows'} <= set(names)
import json; print(json.dumps([t['name'] for t in s.TOOLS if t['name'] == 'click'][0]['inputSchema']['properties'].get('points'), ensure_ascii=False))
print('OK')
"
```
Expected: 工具数 24(原 **20** + 4;设计初稿误把 SERVER_INFO 的 `computer` 也算成工具, 见 §8),包含 `move`/`zoom`/`zoom_to_screen`/`list_windows`,且原有全部工具名仍在;
`click` 的 `button` enum 已含 `x1/x2`,`click`/`move` 的 schema 已含 `points`/`gap_ms`(上面那行 json 应打印出 points 的定义而非 null)。

- [ ] **Step 5: 更新 README**

在"模型可用动作"表与"DSH MCP 插件集成"表里补 `move`/`zoom`/`zoom_to_screen`/`list_windows` 四行,并写明 `click`/`move` 的新参数(`hold_ms`/`duration_ms`/`points`/`gap_ms`),以及"截图内像素 vs 屏幕物理像素"的口径区别与坐标反算工作流。

- [ ] **Step 6: 提交**

```bash
git add computer_mcp/server.py ai_mode/controller.py README.md
git commit -m "新增:MCP 工具 move/zoom/zoom_to_screen/list_windows, click 与 move 支持执行时间与带间隔的坐标序列"
```

---

## 5. 未打通项与风险(不要假装完成)

| 项 | 状态 | 现象 | 下一步 |
|---|---|---|---|
| UIA 控件级遍历 | **未打通** | `element.FindAll(scope,cond,&arr)` 恒返回 E_FAIL(-2147467259),scope=Children/Descendants 均失败 | 先用正确索引拿 `CreateTrueCondition`(推算 15,存疑)或 `get_ControlViewCondition`;再验证 `IUIAutomationElementArray::get_Length(3)/GetElement(4)`。或改用 `FindFirst(5)` 逐步下探 |
| UIA TreeWalker | **未定位索引** | `get_RawViewWalker` 推算在 41~46,实测 41 返回 null、42/43 access violation | 用 `IUIAutomation::CreateTreeWalker(cond,&walker)`(推算索引 10)替代 getter |
| element 属性完整性 | 部分验证 | 21/23/29/30/43 已验证;`IsEnabled`/`IsOffscreen`/`ProcessId` 等未验证 | 需要时按同一方法先探测再使用 |
| BSTR 内存 | 已知泄漏 | 用 `wstring_at` 读后未 `SysFreeString` | 后续加 `oleaut32.SysFreeString` |
| 放大镜多屏 | 未验证 | `clamp_region` 目前按单屏尺寸;多屏负坐标(`rect=(-8,-8,...)` 已在实测中出现)可能夹取错误。**反算受同一问题影响**(screen_rect 本身就可能夹错) | 需要时用 `SM_XVIRTUALSCREEN`/`SM_CXVIRTUALSCREEN` 取虚拟屏边界 |
| 反算的时效性 | 已知局限(设计如此) | `zoom_to_screen` 用的是"最近一次 zoom"的映射,期间画面/窗口位置变化会让结果过时;并发调用时后一次 zoom 会覆盖前一次 | 工具描述与 AI 动作表已写明"画面变化要重新 zoom";不做自动失效检测(需要屏幕哈希,成本高) |
| AI 模式 zoom 无图像 | 已知限制 | `ai_mode` 的动作级结果只回文本, 放大图进不了对话;该动作返回 meta + 提示 | 需要看图用 `take_screenshot`, 或走 MCP 通道的 `zoom` |
| UIA 元素引用泄漏 | 已知(审查发现) | `element_from_hwnd` 取到的 `IUIAutomationElement` 从不 `Release`,每次枚举每个窗口泄漏一个对象 | 需要时按同一方法验证 `IUnknown::Release`(idx=2)后补上;当前单次 `list_windows` 的泄漏量可忽略 |
| 侧键被驱动映射 | 环境相关 | 部分鼠标厂商驱动把 x1/x2 硬映射为前进/后退 | 文档说明即可,无法绕过 |

**风险**:UIA 通道对 Electron/Chromium 应用(实测里 `Chrome_WidgetWin_1`)的**控件树**深度有限,且需要应用开启无障碍支持。**游戏/Canvas 场景必须回退到截图 + 放大镜**。

---

## 6. 验证清单(2026-09-11 实施后逐条核对)

**已自动验证(以实测输出为准):**

- [x] `python -m unittest discover tests -v` 全绿 —— **48 tests OK**(mouse 18 / service 11 / MCP 分派 3 / observe 13 / uia 3)
- [x] 旧调用 `svc.click(x, y, button="right", clicks=2)` 与 `mouse.click("right", 100, 200)` 行为不变(单测覆盖;`ComputerService().get_state()` 正常返回)
- [x] `svc.click()`(无坐标)不产生任何移动调用;`svc.move()` 不产生任何点击调用
- [x] `points` 与 `x`/`y`/`at` 同时传入抛 `ValueError`;空列表、长度为 1/4 的元素、非数字、`gap_ms<0`、三元时间值越界都抛 `ValueError`
- [x] `zoom()` 用了 NEAREST:实测同一区域放大前后颜色数相等(84 == 84),细线未被插值糊掉
- [x] `zoom()` 的 meta 含 `screen_rect/factor/src_size/out_size/cursor/seq/to_screen/from_screen`;`src=100, factor=10` 输出 1000×1000
- [x] `px_to_screen(*screen_to_px(sx, sy, meta), meta) == (sx, sy)` —— 往返实测回到 `(1068, 815)`(鼠标位置)
- [x] `zoom_to_screen` 像素越界抛 `ValueError`(错误信息含真实 `out_size`)、未 zoom 时抛 `RuntimeError`(单测)
- [x] `UIA().list_windows()` 实测列出 7 个可见顶层窗口(含 hwnd/type/rect/class/name)
- [x] MCP `TOOLS` 实测 **24** 个,原有 20 个工具名全部保留;`click` 的 button enum 已含 `x1/x2`,`hold_ms`/`points`/`gap_ms` 都在 schema 里
- [x] MCP `zoom` 返回 `image`+`text`(text 为 `zoom_meta`);`zoom_to_screen`/`list_windows` 返回文本
- [x] `ai_mode` 的 `SYSTEM_PROMPT` 原有 19 种动作一行未删(只追加)
- [x] `git status` 干净(测试产物已进 `.gitignore`)

**待人工验证(会真的移动/点击鼠标, 未自动执行):**

- [ ] `move(points=[[..],[..]], duration_ms=400, gap_ms=300)` 走完每个点、每段约 400ms、点间停约 300ms、**最后一点后不再等待**;三元点的 `duration_ms` 覆盖函数级值
- [ ] `click(points=[[..],[..]], hold_ms=150, gap_ms=500)` 每点按压约 150ms、点间停约 500ms;`interval_ms`(双击间隔)未被 `gap_ms` 影响
- [ ] `ComputerService().click(100,100,button='x1',hold_ms=200,clicks=2)` 真实触发侧键与长按
- [ ] 按"zoom → zoom_to_screen → click"闭环点中放大图里选中的小目标

---

## 7. 附:环境与复现

### 7.1 关键常量速查

```
CLSID_CUIAutomation  = {FF48DBA4-60EF-4201-AA87-54103EEF594E}
IID_IUIAutomation    = {30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}
CLSCTX_INPROC_SERVER = 1

vtable 已验证:
  IUIAutomation        : 5=GetRootElement, 6=ElementFromHandle
  IUIAutomationElement : 21=ControlType, 23=Name, 29=AutomationId,
                         30=ClassName, 43=BoundingRectangle

pynput Button 枚举(实测) : ['unknown','left','middle','right','x1','x2']
GetDoubleClickTime 默认  : 500 ms
HOLD_MS_MAX              : 5000 (click 按压时长上限)
DOUBLE_CLICK_INTERVAL_MS : 80   (click 的 interval_ms 默认值, 同一次点击内部)
GAP_MS_DEFAULT           : 0    (points 序列相邻两点间歇, move/click 的 gap_ms 默认值)
```

### 7.2 运行本项目代码的前提

DSH 沙箱下必须提权到 `danger-full-access`(见 §3.9),否则 `vendor/pynput` 不可枚举、`input_engine` 导入失败。沙箱外正常运行不受影响:

```bash
cd H:\cu-a
python main.py
```

### 7.3 本次探测脚本(过程留档)

| 脚本 | 用途 | 结果 |
|---|---|---|
| `.tmp/uia_probe2.py` | CoInitialize + CUIAutomation + 前台窗口属性 | 通过 |
| `.tmp/uia_probe3.py` | CreateTrueCondition(15) / BoundingRectangle(43) / FindAll | 前两项通过,FindAll E_FAIL |
| `.tmp/uia_probe4.py` | TreeWalker 索引试探 | 未定位(41=null, 42/43 崩) |
| `.tmp/uia_probe5.py` | EnumWindows + ElementFromHandle 窗口级观察 | **通过(8 窗口)** |
| `.tmp/uia_probe6.py` | 条件对象来源试探 | 未通过(11/12/13 崩) |
| `.tmp/mouse_probe.py` | pynput Button 枚举 | 需提权后通过(x1/x2 存在) |

> ✅ **已处理**(commit `e25558a`):`.gitignore` 增补了 `.tmp/`、`work/`、`catgirl_happy_chibi.png`、`angry_bird_riding_bike.png`、`*测试*.png`、`表情包_*.png`;`git status` 现在干净。开工时的落地脚本留在 `.tmp/`(已忽略):`verify_zoom.py`、`smoke_mcp.py`、`verify_service_observe.py`。

---

## 8. 实施记录(2026-09-11 落地, 与设计初稿的偏差)

**提交链(中文 commit, 一次提交一个逻辑改动):**

| commit | 内容 |
|---|---|
| `e25558a` | 构建:gitignore 忽略测试产物(`.tmp/`、`work/`、试画 PNG) |
| `417e8e5` | Task 1 输入层:移动/点击独立 + 执行时间 + 坐标序列 |
| `a4c7c7e` | Task 2 服务层:click 扩展(hold_ms/侧键/序列) + 独立 move |
| `4b54150` | Task 3 放大镜 observe.py + 像素反算 |
| `1427638` | Task 4 UIA 窗口级观察 uia.py |
| `c8f8823` | 服务层封装观察能力(zoom_tool/zoom_to_screen/describe_windows) |
| `1dffa08` | Task 5 MCP 工具 move/zoom/zoom_to_screen/list_windows + AI 动作表 + README |

**落地偏差(实现与初稿不同, 一律以代码为准):**

1. **`mouse.click` 保留 `x`/`y` 位置参数**。`input_engine/mouse.py` 原本已有 `click(button, x, y)`, 且 `computer_core/service.py` 与 `modes/work.py` 在调用。初稿把 `hold_ms` 放第二位会让 `click("right", 100, 200)` **静默错位**,因此实际签名是 `click(button="left", x=None, y=None, hold_ms=0, clicks=1, interval_ms=None, at=None, points=None, gap_ms=0, cfg=None)`,新增参数一律关键字传入。
2. **MCP 坐标口径统一为"截图内像素"**。初稿要求 `zoom`/`zoom_to_screen` 走屏幕物理坐标,但 MCP 其余工具都是截图内像素,模型混用必错。实际做法:换算只发生在 server 边界,`zoom_to_screen` 同时返回 `screen_x/screen_y` 与 `img_x/img_y`。
3. **工具数是 24 而非 25**。初稿把 `SERVER_INFO` 里的 `"name": "computer"` 也当成了工具;`TOOLS` 实际 **20** 个,加 4 个新工具后为 24。
4. **Task 1 单测 18 个而非 17** —— 多了一个"旧位置参数兼容"用例。全量 **41 tests**。
5. **`zoom`/`list_windows` 在 `handle_tool_call` 特判**(返回图像/整段文本),不走 `_run_single`;`move`/`click`/`zoom_to_screen` 走统一执行器,因此 `run_actions` 里也能批量调用 `move`/`zoom_to_screen`。
6. **AI 模式的 `zoom` 只回元数据**,放大图未接入动作级对话(见 §5)。
7. **新增 `tests/__init__.py`**,让 `python -m unittest discover tests` 与 `python -m unittest tests.test_xxx` 两种方式都能跑。

**审查后修复(2026-09-11, commit `cd462d5`;由独立只读审查子代理过了一遍全部 diff):**

| # | 缺陷 | 修法 |
|---|---|---|
| 1 | MCP/AI 的 `click` 省略坐标时走 `action.get("x", 0)`, 会真的移到 (0,0) 再点, 与工具描述承诺的"原地点击"矛盾 | 坐标任一缺失即调 `svc.click(**kw)`(不传位置参数), 原地点击 |
| 2 | `svc.click(move_mode="smooth")` 丢掉了 `duration_ms`, 平滑定位耗时不可控 | `service.click` 增加 `duration_ms` 并透传给 `mouse.move`;MCP `click` schema 同步暴露 `move_mode`/`duration_ms` |
| 3 | 服务层缺校验: 单点路径接受 `gap_ms<0`、只给 x 或只给 y 会静默原地点击、`points` 与 `x/y` 同给时静默取 points | 服务层统一抛 `ValueError`(三条校验) |
| 4 | `zoom_to_screen` 的 `img_x/img_y` 依赖"最近一次截图尺寸", 模型无法判断口径 | 返回值补 `img_size`, note 改为"img_x/img_y 是 img_size 口径的截图坐标" |

新增 `tests/test_mcp_dispatch.py`(3 个用例, 直接验证 MCP 分派, 用 mock 不真的点击)。测试总数 41 → **48**。

**审查发现但未修(已记入 §5):** UIA 元素引用未 `Release`、BSTR 未 `SysFreeString`、`CoUninitialize` 未调用 —— 均为泄漏类问题, 单次会话量级可忽略, 需要时按实测索引补。

**实测环境**: Python 3.14.3 / Pillow 12.3.0 / pynput 1.8.2 / 屏幕 1920×1080 / DSH `danger-full-access`。
