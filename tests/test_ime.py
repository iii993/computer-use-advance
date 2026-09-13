# tests/test_ime.py
import sys, os, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))

from input_engine import ime


class TestDescribeLayout(unittest.TestCase):
    def test_english_us(self):
        d = ime.describe_layout(0x04090409)
        self.assertEqual(d["lang_id"], "0x0409")
        self.assertTrue(d["is_english"])
        self.assertFalse(d["is_chinese"])

    def test_chinese_simplified(self):
        d = ime.describe_layout(0x08040804)
        self.assertTrue(d["is_chinese"])
        self.assertFalse(d["is_english"])

    def test_unknown_layout(self):
        d = ime.describe_layout(0x1234ABCD)
        self.assertIn("未知", d["name"])
        self.assertFalse(d["is_chinese"])

    def test_zero_hkl(self):
        self.assertEqual(ime.describe_layout(0)["hkl"], 0)


class TestSwitchTo(unittest.TestCase):
    def test_unknown_layout_raises(self):
        with self.assertRaises(ValueError):
            ime.switch_to("klingon")

    def test_post_message_when_hwnd_given(self):
        with mock.patch.object(ime._user32, "LoadKeyboardLayoutW", return_value=0x04090409), \
             mock.patch.object(ime._user32, "PostMessageW", return_value=1) as pm, \
             mock.patch.object(ime, "current_layout",
                               return_value={"hkl": 0x08040804, "is_english": False}):
            r = ime.switch_to("en", hwnd=123)
            self.assertTrue(r["ok"])
            pm.assert_called_once()
            self.assertEqual(pm.call_args.args[1], ime.WM_INPUTLANGCHANGEREQUEST)

    def test_activate_when_no_hwnd(self):
        with mock.patch.object(ime._user32, "LoadKeyboardLayoutW", return_value=0x04090409), \
             mock.patch.object(ime._user32, "ActivateKeyboardLayout", return_value=0x04090409) as ak, \
             mock.patch.object(ime, "current_layout", return_value={"hkl": 0x08040804}):
            self.assertTrue(ime.switch_to("en")["ok"])
            ak.assert_called_once()

    def test_ensure_english_skips_when_already_english(self):
        with mock.patch.object(ime, "current_layout",
                               return_value={"hkl": 0x04090409, "is_english": True}), \
             mock.patch.object(ime, "switch_to") as sw:
            r = ime.ensure_english()
            self.assertFalse(r["changed"])
            sw.assert_not_called()

    def test_ensure_english_switches_when_chinese(self):
        with mock.patch.object(ime, "current_layout",
                               return_value={"hkl": 0x08040804, "is_english": False}), \
             mock.patch.object(ime, "switch_to",
                               return_value={"ok": True, "method": "x"}) as sw:
            r = ime.ensure_english()
            self.assertTrue(r["changed"])
            sw.assert_called_once()


if __name__ == "__main__":
    unittest.main()
