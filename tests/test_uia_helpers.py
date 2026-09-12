# tests/test_uia_helpers.py
import sys, os, unittest
from unittest import mock
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


class TestFocusSemantics(unittest.TestCase):
    """真机暴露: 窗口已经在前台时 SetForegroundWindow 会失败, 但那不算失败。"""

    def _u(self):
        return uia.UIA.__new__(uia.UIA)

    def test_already_foreground_counts_as_success(self):
        with mock.patch.object(uia._user32, "GetForegroundWindow", return_value=42), \
             mock.patch.object(uia._user32, "IsIconic", return_value=False):
            self.assertTrue(self._u().focus(42))

    def test_calls_set_foreground_when_not_foreground(self):
        with mock.patch.object(uia._user32, "GetForegroundWindow", return_value=7), \
             mock.patch.object(uia._user32, "IsIconic", return_value=False), \
             mock.patch.object(uia._user32, "SetForegroundWindow", return_value=1) as sf:
            self.assertTrue(self._u().focus(42))
            sf.assert_called_once()

    def test_minimized_window_is_restored_first(self):
        with mock.patch.object(uia._user32, "GetForegroundWindow", return_value=7), \
             mock.patch.object(uia._user32, "IsIconic", return_value=True), \
             mock.patch.object(uia._user32, "ShowWindow", return_value=1) as sw, \
             mock.patch.object(uia._user32, "SetForegroundWindow", return_value=1):
            self._u().focus(42)
            self.assertEqual(sw.call_args.args[1], uia.SW_RESTORE)


class TestFindWindowRanking(unittest.TestCase):
    """真机暴露: title="Krita" 曾匹配到 "krita.e - Everything" 窗口 —— 子串匹配太宽松。"""

    WINDOWS = [
        {"hwnd": 1, "name": "krita.e - Everything", "class": "EVERYTHING",
         "win32_title": "krita.e - Everything", "rect": (0, 0, 0, 0)},
        {"hwnd": 2, "name": "无标题 - Krita", "class": "KDockMainWindow",
         "win32_title": "无标题 - Krita", "rect": (10, 10, 800, 600)},
        {"hwnd": 3, "name": "Chrome_WidgetWin_1", "class": "Chrome_WidgetWin_1",
         "win32_title": "krita 教程 - 浏览器", "rect": (0, 0, 1200, 800)},
    ]

    def _u(self, windows=None):
        u = uia.UIA.__new__(uia.UIA)
        u.list_windows = lambda *a, **k: list(windows if windows is not None else self.WINDOWS)
        return u

    def test_word_boundary_beats_loose_prefix(self):
        # "无标题 - Krita"(词边界) 应胜过 "krita.e - Everything"(仅前缀)
        self.assertEqual(self._u().find_window(title="Krita")["hwnd"], 2)

    def test_exact_name_wins_over_everything(self):
        w = [{"hwnd": 9, "name": "Krita", "class": "X", "win32_title": "Krita", "rect": (0, 0, 10, 10)},
             {"hwnd": 8, "name": "无标题 - Krita", "class": "Y", "win32_title": "无标题 - Krita", "rect": (0, 0, 10, 10)}]
        self.assertEqual(self._u(w).find_window(title="krita")["hwnd"], 9)

    def test_with_candidates_reports_ambiguity(self):
        best, others = self._u().find_window(title="Krita", with_candidates=True)
        self.assertEqual(best["hwnd"], 2)
        hwnds = [o["hwnd"] for o in others]
        self.assertIn(1, hwnds)                 # 宽松匹配到的窗口要能被告知
        self.assertNotIn(2, hwnds)

    def test_no_match_returns_none_pair(self):
        best, others = self._u().find_window(title="不存在", with_candidates=True)
        self.assertIsNone(best)
        self.assertEqual(others, [])


if __name__ == "__main__":
    unittest.main()