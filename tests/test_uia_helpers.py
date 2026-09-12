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


class TestFindWindow(unittest.TestCase):
    """find_window 的匹配逻辑(不初始化 COM: 直接构造实例并替换 list_windows)。"""

    WINDOWS = [
        {"hwnd": 1, "name": "记事本", "class": "Notepad", "win32_title": "无标题 - 记事本",
         "rect": (0, 0, 100, 100)},
        {"hwnd": 2, "name": "Chrome", "class": "Chrome_WidgetWin_1", "win32_title": "百度一下"},
        {"hwnd": 3, "name": "", "class": "EVERYTHING", "win32_title": "Everything"},
    ]

    def _u(self):
        u = uia.UIA.__new__(uia.UIA)          # 绕过 __init__, 避免真实 COM
        u.list_windows = lambda *a, **k: list(self.WINDOWS)
        return u

    def test_match_by_name_substring(self):
        self.assertEqual(self._u().find_window(title="记事")["hwnd"], 1)

    def test_match_by_win32_title_when_name_empty(self):
        self.assertEqual(self._u().find_window(title="everything")["hwnd"], 3)

    def test_match_is_case_insensitive(self):
        self.assertEqual(self._u().find_window(title="CHROME")["hwnd"], 2)

    def test_match_by_hwnd(self):
        self.assertEqual(self._u().find_window(hwnd=2)["hwnd"], 2)

    def test_no_match_returns_none(self):
        self.assertIsNone(self._u().find_window(title="不存在的窗口"))


if __name__ == "__main__":
    unittest.main()