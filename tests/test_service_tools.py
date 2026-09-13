# tests/test_service_tools.py
import sys, os, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))

from computer_core import service, uia


KRITA_WIN = {"hwnd": 1, "name": "Krita", "class": "Qt5157QWindowIcon",
             "win32_title": "Krita", "rect": (0, 0, 960, 540),
             "control_type_name": "Window"}
EVERYTHING_WIN = {"hwnd": 2, "name": "krita.e - Everything", "class": "EVERYTHING",
                  "win32_title": "krita.e - Everything", "rect": (0, 0, 0, 0),
                  "control_type_name": "Window"}


def _uia_with(seq):
    """构造一个真实 UIA 实例, 只替换 list_windows(按 seq 依次返回)。"""
    u = uia.UIA.__new__(uia.UIA)
    box = {"i": 0}

    def _lw(*a, **k):
        i = min(box["i"], len(seq) - 1)
        box["i"] += 1
        return seq[i]

    u.list_windows = _lw
    u.calls = box
    return u


class TestFocusWindow(unittest.TestCase):
    def _svc(self):
        return service.ComputerService(config={"ai": {}, "draw": {}})

    def test_requires_hwnd_or_title(self):
        svc = self._svc()
        with self.assertRaises(ValueError):
            svc.focus_window()

    def test_unknown_title_reports_error(self):
        svc = self._svc()
        fake = mock.Mock()
        fake.find_window.return_value = (None, [])
        fake.list_windows.return_value = [{"hwnd": 1, "name": "x", "win32_title": "x"}]
        svc._uia = fake
        r = svc.focus_window(title="不存在的窗口")
        self.assertFalse(r["ok"])
        self.assertIn("候选", r["error"])
        self.assertEqual(r["attempts"], 1)

    def test_focus_by_hwnd(self):
        svc = self._svc()
        fake = mock.Mock()
        fake.focus.return_value = True
        fake.find_window.return_value = (
            {"hwnd": 7, "name": "记事本", "class": "Notepad",
             "win32_title": "记事本", "rect": (0, 0, 100, 100)},
            [{"hwnd": 8, "name": "别的窗口", "class": "X"}])
        svc._uia = fake
        r = svc.focus_window(hwnd=7)
        self.assertTrue(r["ok"])
        self.assertEqual(r["hwnd"], 7)
        self.assertEqual(r["other_candidates"][0]["hwnd"], 8)

    # ---- 新增: 启动后窗口要等一会儿才出现(真机踩坑) ----
    def test_polls_until_window_appears(self):
        svc = self._svc()
        fake = mock.Mock()
        fake.focus.return_value = True
        fake.find_window.side_effect = [
            (None, []), (None, []),
            ({"hwnd": 7, "name": "Krita", "class": "Qt", "win32_title": "Krita",
              "rect": (0, 0, 10, 10)}, [])]
        svc._uia = fake
        r = svc.focus_window(title="Krita", wait_seconds=5, poll_interval=0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["hwnd"], 7)
        self.assertEqual(r["attempts"], 3)

    def test_no_wait_makes_single_attempt(self):
        svc = self._svc()
        fake = mock.Mock()
        fake.find_window.return_value = (None, [])
        fake.list_windows.return_value = []
        svc._uia = fake
        r = svc.focus_window(title="Krita")
        self.assertFalse(r["ok"])
        self.assertEqual(r["attempts"], 1)

    def test_wait_gives_up_after_deadline(self):
        svc = self._svc()
        fake = mock.Mock()
        fake.find_window.return_value = (None, [])
        fake.list_windows.return_value = []
        svc._uia = fake
        r = svc.focus_window(title="Krita", wait_seconds=0.05, poll_interval=0)
        self.assertFalse(r["ok"])
        self.assertGreater(r["attempts"], 1)
        self.assertLess(r["waited_ms"], 3000)


class TestDescribeWindows(unittest.TestCase):
    def _svc(self):
        return service.ComputerService(config={"ai": {}, "draw": {}})

    def test_title_filter_keeps_only_matches(self):
        svc = self._svc()
        svc._uia = _uia_with([[KRITA_WIN, EVERYTHING_WIN]])
        out = svc.describe_windows(title="Krita")
        self.assertIn("hwnd=1", out)
        self.assertNotIn("hwnd=2", out)

    def test_no_match_message(self):
        svc = self._svc()
        svc._uia = _uia_with([[]])
        out = svc.describe_windows(title="Krita")
        self.assertIn("没有匹配", out)

    def test_waits_for_match(self):
        svc = self._svc()
        svc._uia = _uia_with([[], [], [KRITA_WIN]])
        out = svc.describe_windows(title="Krita", wait_seconds=2, poll_interval=0)
        self.assertIn("hwnd=1", out)

    def test_plain_list_without_title(self):
        svc = self._svc()
        svc._uia = _uia_with([[KRITA_WIN, EVERYTHING_WIN]])
        out = svc.describe_windows()
        self.assertIn("hwnd=1", out)
        self.assertIn("hwnd=2", out)


class TestSendText(unittest.TestCase):
    def _svc(self):
        return service.ComputerService(config={"ai": {}, "draw": {}})

    def test_types_then_submits(self):
        svc = self._svc()
        with mock.patch("input_engine.text.type_text") as tt, \
             mock.patch.object(service.keyboard, "combo") as cb:
            rec = svc.send_text("你好呀", submit_keys=["ctrl", "enter"])
            tt.assert_called_once()
            cb.assert_called_once_with(["ctrl", "enter"])
            self.assertEqual(rec["text_len"], 3)
            self.assertEqual(rec["submit_keys"], ["ctrl", "enter"])

    def test_without_submit_only_types(self):
        svc = self._svc()
        with mock.patch("input_engine.text.type_text") as tt, \
             mock.patch.object(service.keyboard, "combo") as cb:
            rec = svc.send_text("hi")
            tt.assert_called_once()
            cb.assert_not_called()
            self.assertIsNone(rec["submit_keys"])

    def test_clear_first_selects_all_then_deletes(self):
        svc = self._svc()
        with mock.patch("input_engine.text.type_text"), \
             mock.patch.object(service.keyboard, "combo") as cb, \
             mock.patch.object(service.keyboard, "tap") as tp:
            svc.send_text("new", clear_first=True)
            cb.assert_called_once_with(["ctrl", "a"])
            self.assertTrue(tp.called)


if __name__ == "__main__":
    unittest.main()
