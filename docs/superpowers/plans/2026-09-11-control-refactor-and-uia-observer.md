# 控制系统重构 + 观察通道(放大镜 / UIA) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`[ ]`) syntax for tracking.

**Goal:** 把 `input_engine` / `computer_core` 的输入控制重构为"移动与点击彻底解耦 + 点击可指定按压时长/按键种类(含侧键)/双击",并新增两条观察通道:10X 鼠标放大镜与 UIA 无障碍树文本观察。

**Architecture:** 输入层(`input_engine/`)只负责原语,不再隐含"移动+点击"的复合行为;`computer_core/service.py` 作为统一服务层适配新旧调用并保持向后兼容;观察能力拆成独立模块 `computer_core/observe.py`(放大镜,基于 Pillow)与 `computer_core/uia.py`(基于纯 ctypes 直调 Windows UI Automation,零第三方依赖),最后在 `computer_mcp/server.py` 暴露为 MCP 工具。

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

### 1.2 本次要解决的三个问题

1. **移动与点击耦合**:AI 无法"先移动到目标 → 截图确认 → 再点击"。现有 `click` 一步做完,期间画面可能已变。
2. **点击能力单一**:只有 `clicks=1/2` 和 `left/right/middle`。缺按压时长(长按/拖住)、侧键(X1/X2)、可控双击间隔。
3. **观察通道单一**:只有全屏截图。缺"看清小目标"的能力(放大镜),也缺"精确知道有哪些窗口/控件"的能力(UIA 文本)。

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


def move(x, y, mode="smooth", duration_ms=None, cfg=None):
    """只移动,不点击。mode='smooth'(插值+抖动+加速) | 'instant'(瞬移)。"""


def click(button="left", hold_ms=0, clicks=1, interval_ms=None, at=None, cfg=None):
    """只点击。at=(x,y) 给了就先移动(默认瞬移,保持旧行为)。"""
```

行为定义:

- `move(mode="smooth")` 复用现有 `move_to()`;`move(mode="instant")` 等价现有 `move_absolute()`。
- `click(hold_ms=0)` 走 `mc.click(btn, 1)`;`hold_ms>0` 走 `press → sleep(hold_ms/1000) → release`。
- `hold_ms` 越界(负数或 > `HOLD_MS_MAX`)**抛 `ValueError`**,不静默夹取。
- `clicks>1` 时两次点击之间 `sleep(interval_ms/1000)`,`interval_ms` 默认 `DOUBLE_CLICK_INTERVAL_MS`。
- 未知 `button` 抛 `ValueError`,错误信息里列出全部合法值。

> **双击语义说明**:Windows 判定双击依赖 `GetDoubleClickTime()`(默认 500ms)。本实现固定 80ms 间隔,**远小于系统阈值**,因此系统会识别为双击。这一点写进工具描述,避免模型猜。

### 2.2 服务层适配(`computer_core/service.py`)

`ComputerService.click` 保持旧签名可用,新增可选参数:

```python
def click(self, x=None, y=None, button="left", clicks=1,
          hold_ms=0, interval_ms=None, move_mode="instant"):
    """at 语义: x/y 都给则先移动(默认瞬移);都不给则原地点击。"""
```

新增薄封装:`move(x, y, mode="smooth")`、`zoom(...)`(见 2.3)、`describe_windows()`(见 2.4)。

**兼容验证点**:现有调用 `svc.click(x, y, button="right", clicks=2)` 必须行为不变(先瞬移再双击)。

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
  "to_screen": "screen_x = screen_rect[0] + px/factor"
}
```

否则模型拿放大图里的像素坐标直接去点击,必然全错。

边界处理:`bbox` 超出屏幕时要夹取到屏幕内(多屏用 `all_screens=True`),并把**夹取后的**真实矩形写进 `screen_rect`。

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

---

## 4. 任务分解

> 项目当前**没有测试基础设施**。为避免新增依赖,测试统一用标准库 `unittest`,放 `tests/`,用 `python -m unittest` 运行。只对**纯函数**(坐标换算、参数校验、映射表)写单元测试;真实鼠标/键盘行为用人工验证步骤。

### Task 1: 输入层原语(移动与点击解耦)

**Files:**
- Modify: `input_engine/mouse.py`
- Test: `tests/test_mouse_input.py` (新建)

**Interfaces:**
- Consumes: 现有 `mc = MouseController()`、`move_to()`、`move_absolute()`
- Produces:
  - `BUTTONS: dict[str, pynput.mouse.Button]`
  - `HOLD_MS_MAX: int = 5000`
  - `DOUBLE_CLICK_INTERVAL_MS: int = 80`
  - `move(x, y, mode="smooth", duration_ms=None, cfg=None) -> None`
  - `click(button="left", hold_ms=0, clicks=1, interval_ms=None, at=None, cfg=None) -> None`

- [ ] **Step 1: 写失败测试**(纯函数部分,不碰真实鼠标)

```python
# tests/test_mouse_input.py
import sys, os, unittest
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

HOLD_MS_MAX = 5000
DOUBLE_CLICK_INTERVAL_MS = 80


def move(x: float, y: float, mode: str = "smooth",
         duration_ms: float | None = None, cfg: dict | None = None) -> None:
    """只移动,不点击。mode='smooth' 插值+抖动+加速; 'instant' 瞬移。"""
    if mode == "instant":
        mc.position = (x, y)
        return
    if mode != "smooth":
        raise ValueError(f"未知 move mode: {mode} (可选 smooth/instant)")
    move_to(x, y, cfg, duration_ms)


def click(button: str = "left", hold_ms: float = 0, clicks: int = 1,
          interval_ms: float | None = None, at: tuple | None = None,
          cfg: dict | None = None) -> None:
    """只点击(除非给了 at)。hold_ms 为按压时长(毫秒), 上限 HOLD_MS_MAX。"""
    btn = BUTTONS.get(button)
    if btn is None:
        raise ValueError(f"未知按键: {button} (可选 {sorted(BUTTONS)})")
    if hold_ms < 0 or hold_ms > HOLD_MS_MAX:
        raise ValueError(f"hold_ms 必须在 0~{HOLD_MS_MAX} 之间, 收到 {hold_ms}")
    if clicks < 1:
        raise ValueError(f"clicks 必须 >= 1, 收到 {clicks}")
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
Expected: PASS (4 tests)

> 沙箱提示:此步需要 DSH 提权到 `danger-full-access`(见 §3.9)。

- [ ] **Step 5: 人工验证侧键与长按**(需要手动执行,鼠标会真的动)

```bash
python -c "import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']; from input_engine import mouse; import time; time.sleep(2); mouse.click(button='x1'); print('侧键 x1 已发送')"
```
Expected: 浏览器/资源管理器中触发"后退"。

- [ ] **Step 6: 提交**

```bash
git add input_engine/mouse.py tests/test_mouse_input.py
git commit -m "重构:鼠标输入层移动与点击解耦, 点击支持按压时长/侧键/双击间隔"
```

### Task 2: 服务层适配与向后兼容

**Files:**
- Modify: `computer_core/service.py` (`click` 方法,约 105 行附近)
- Test: `tests/test_service_click.py` (新建)

**Interfaces:**
- Consumes: Task 1 的 `mouse.move()` / `mouse.click()`
- Produces: `ComputerService.click(x=None, y=None, button="left", clicks=1, hold_ms=0, interval_ms=None, move_mode="instant")` 与 `ComputerService.move(x, y, mode="smooth")`

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_service_click -v`
Expected: FAIL — `TypeError: click() got an unexpected keyword argument 'hold_ms'`

- [ ] **Step 3: 实现**

```python
def click(self, x=None, y=None, button: str = "left", clicks: int = 1,
          hold_ms: float = 0, interval_ms: float | None = None,
          move_mode: str = "instant"):
    """点击。x/y 都给则先移动(默认瞬移), 否则原地点击。"""
    at = (x, y) if (x is not None and y is not None) else None
    mouse.click(button=button, hold_ms=hold_ms, clicks=clicks,
                interval_ms=interval_ms, at=at)
    log.info("click %s hold=%sms clicks=%d at=%s", button, hold_ms, clicks, at)


def move(self, x: float, y: float, mode: str = "smooth"):
    """只移动, 不点击。mode='smooth'|'instant'。"""
    mouse.move(x, y, mode=mode)
    log.info("move %s -> (%.0f, %.0f)", mode, x, y)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_service_click -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 回归验证旧调用路径**

Run: `python -c "import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']; from computer_core.service import ComputerService; s=ComputerService(); print(s.get_state())"`
Expected: 打印 `{'mode': 'work', 'cursor': [...], 'brush_size': 1, 'pen_channel': False}`,无异常。

- [ ] **Step 6: 提交**

```bash
git add computer_core/service.py tests/test_service_click.py
git commit -m "重构:服务层点击支持按压时长/侧键/双击, 新增独立 move 接口"
```

### Task 3: 放大镜观察工具

**Files:**
- Create: `computer_core/observe.py`
- Test: `tests/test_observe_zoom.py` (新建)

**Interfaces:**
- Produces: `zoom(x=None, y=None, factor=10, src=100, quality=85, max_out=1200) -> (bytes, dict)`;`clamp_region(cx, cy, src, screen_size) -> tuple`;`effective_factor(src, factor, max_out) -> int`

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_observe_zoom -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'computer_core.observe'`

- [ ] **Step 3: 实现**

```python
"""观察通道: 放大镜(zoom)。坐标系: 屏幕物理像素。"""
import io
import os
import sys

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
        "to_screen": "screen_x = screen_rect[0] + px/factor",
    }
    return buf.getvalue(), meta
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_observe_zoom -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 真实截图人工验证**

```bash
python -c "import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']; from computer_core.observe import zoom; b,m=zoom(factor=10); open(r'H:\cu-a\.tmp\zoom_check.jpg','wb').write(b); print(m)"
```
Expected: 生成 `.tmp/zoom_check.jpg`,meta 里 `factor=10`、`screen_rect` 为 100×100 的屏幕矩形。打开图片确认**无插值模糊**(细线边缘是硬边)。

- [ ] **Step 6: 提交**

```bash
git add computer_core/observe.py tests/test_observe_zoom.py
git commit -m "新增:10X 鼠标放大镜观察工具(NEAREST 插值+坐标映射回传)"
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
- Produces: MCP 工具 `move`、`zoom`、`list_windows`;扩展后的 `click`(新增 `hold_ms`,`button` 增加 `x1/x2`)

- [ ] **Step 1: 新增 MCP 工具定义**

在 `TOOLS` 列表中追加(不改动现有条目的 name/参数):

```python
{"name": "move", "description": "只移动鼠标, 不点击(mode=smooth 平滑/instant 瞬移).",
 "inputSchema": {"type": "object",
                 "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                "mode": {"type": "string", "enum": ["smooth", "instant"]}},
                 "required": ["x", "y"]}},
{"name": "zoom", "description": "放大鼠标附近或指定坐标周围区域(默认10X, NEAREST 插值). 返回图像 + screen_rect/factor, 用 screen_x = screen_rect[0] + px/factor 换算回屏幕坐标.",
 "inputSchema": {"type": "object",
                 "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                "factor": {"type": "integer"},
                                "src": {"type": "integer"}}}},
{"name": "list_windows", "description": "列出当前可见顶层窗口(UIA 无障碍树, 返回文本). 含 hwnd/名称/类名/矩形/控件类型.",
 "inputSchema": {"type": "object", "properties": {}}},
```

并在现有 `click` 工具的 schema 中**只增加**两个可选属性:

```python
"hold_ms": {"type": "number", "description": "按压时长(毫秒), 0~5000, 默认 0"},
# button 的 enum 由 ["left","right","middle"] 扩展为 ["left","right","middle","x1","x2"]
```

- [ ] **Step 2: 在 `_run_single` 中分派**

```python
elif act == "move":
    svc.move(_img_x(action["x"]), _img_y(action["y"]),
             action.get("mode", "smooth"))
elif act == "zoom":
    return svc.zoom_tool(action.get("x"), action.get("y"),
                         action.get("factor", 10), action.get("src", 100))
elif act == "list_windows":
    return {"text": _UIA_SESSION.format_windows()}
```

> `zoom` 返回的是**图像**,走 `_image_result(...)`,并把 `meta` 作为附带的 text 块一并返回(模型两者都要读)。

- [ ] **Step 3: 更新 `ai_mode/controller.py` 的 `SYSTEM_PROMPT`**

在动作表中追加两行(不删除任何现有行):

```text
{"action":"move","x":..,"y":..,"mode":"smooth|instant"}  只移动不点击
{"action":"zoom","x":..,"y":..,"factor":10}              放大观察目标附近(坐标用 px/factor + screen_rect[0] 换算)
```

- [ ] **Step 4: 冒烟测试 MCP server**

```bash
python -c "
import sys; sys.path[:0]=[r'H:\cu-a',r'H:\cu-a\vendor']
import computer_mcp.server as s
names=[t['name'] for t in s.TOOLS]
print('工具数:', len(names)); print(names)
assert 'move' in names and 'zoom' in names and 'list_windows' in names
print('OK')
"
```
Expected: 工具数 24(原 21 + 3),包含 `move`/`zoom`/`list_windows`,且原有全部工具名仍在。

- [ ] **Step 5: 更新 README**

在"模型可用动作"表与"DSH MCP 插件集成"表里补 `move`/`zoom`/`list_windows` 三行,并写明 `click` 的新参数。

- [ ] **Step 6: 提交**

```bash
git add computer_mcp/server.py ai_mode/controller.py README.md
git commit -m "新增:MCP 工具 move/zoom/list_windows, click 支持按压时长与侧键"
```

---

## 5. 未打通项与风险(不要假装完成)

| 项 | 状态 | 现象 | 下一步 |
|---|---|---|---|
| UIA 控件级遍历 | **未打通** | `element.FindAll(scope,cond,&arr)` 恒返回 E_FAIL(-2147467259),scope=Children/Descendants 均失败 | 先用正确索引拿 `CreateTrueCondition`(推算 15,存疑)或 `get_ControlViewCondition`;再验证 `IUIAutomationElementArray::get_Length(3)/GetElement(4)`。或改用 `FindFirst(5)` 逐步下探 |
| UIA TreeWalker | **未定位索引** | `get_RawViewWalker` 推算在 41~46,实测 41 返回 null、42/43 access violation | 用 `IUIAutomation::CreateTreeWalker(cond,&walker)`(推算索引 10)替代 getter |
| element 属性完整性 | 部分验证 | 21/23/29/30/43 已验证;`IsEnabled`/`IsOffscreen`/`ProcessId` 等未验证 | 需要时按同一方法先探测再使用 |
| BSTR 内存 | 已知泄漏 | 用 `wstring_at` 读后未 `SysFreeString` | 后续加 `oleaut32.SysFreeString` |
| 放大镜多屏 | 未验证 | `clamp_region` 目前按单屏尺寸;多屏负坐标(`rect=(-8,-8,...)` 已在实测中出现)可能夹取错误 | 需要时用 `SM_XVIRTUALSCREEN`/`SM_CXVIRTUALSCREEN` 取虚拟屏边界 |
| 侧键被驱动映射 | 环境相关 | 部分鼠标厂商驱动把 x1/x2 硬映射为前进/后退 | 文档说明即可,无法绕过 |

**风险**:UIA 通道对 Electron/Chromium 应用(实测里 `Chrome_WidgetWin_1`)的**控件树**深度有限,且需要应用开启无障碍支持。**游戏/Canvas 场景必须回退到截图 + 放大镜**。

---

## 6. 验证清单(全部完成后逐条核对)

- [ ] `python -m unittest discover tests -v` 全绿
- [ ] `ComputerService().click(100,100,button='x1',hold_ms=200,clicks=2)` 不抛异常
- [ ] 旧调用 `svc.click(x, y, button="right", clicks=2)` 行为与重构前一致(先瞬移再双击)
- [ ] `zoom()` 输出图上 1px 细线为硬边(证明用了 NEAREST)
- [ ] `zoom()` 的 `meta.factor` 在 `src` 很大时自动下调,且 `screen_rect` 与实际截图区域一致
- [ ] `UIA().format_windows()` 能列出当前所有可见窗口
- [ ] MCP `TOOLS` 工具数为 24,原有 21 个工具名全部保留
- [ ] `ai_mode` 的 `SYSTEM_PROMPT` 原有 19 种动作一行未删
- [ ] `git status` 干净,无临时文件入库

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

> `.tmp/` 目前**未被 `.gitignore` 覆盖**(现有规则只有 `tmp/`、`vendor/`、`logs/`、`__pycache__/`、`.pip-cache/`、`.venv/`、`screenshot_*.jpg|png`)。开工前应把 `.tmp/`、`work/` 以及根目录那些测试 PNG(`catgirl_happy_chibi.png`、`表情包_*.png` 等)补进 `.gitignore`,否则 `git status` 永远不干净,违反仓库"提交前确认干净"的约定。
