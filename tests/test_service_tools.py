# tests/test_service_tools.py
import sys, os, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))

from computer_core import service


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
        fake.find_window.return_value = None
        fake.list_windows.return_value = [{"hwnd": 1, "name": "x", "win32_title": "x"}]
        svc._uia = fake
        r = svc.focus_window(title="不存在的窗口")
        self.assertFalse(r["ok"])
        self.assertIn("候选", r["error"])

    def test_focus_by_hwnd(self):
        svc = self._svc()
        fake = mock.Mock()
        fake.focus.return_value = True
        fake.find_window.return_value = {"hwnd": 7, "name": "记事本", "class": "Notepad",
                                         "win32_title": "记事本", "rect": (0, 0, 100, 100)}
        svc._uia = fake
        r = svc.focus_window(hwnd=7)
        self.assertTrue(r["ok"])
        self.assertEqual(r["hwnd"], 7)


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
