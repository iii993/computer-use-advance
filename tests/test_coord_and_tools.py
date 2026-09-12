# tests/test_coord_and_tools.py
import sys, os, json, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))

from computer_mcp import server


class TestCoordMode(unittest.TestCase):
    """坐标口径: coord=image(默认, 按最近一次截图换算) / coord=screen(原样使用)。"""

    def setUp(self):
        self._saved = (server._img_w, server._img_h)
        server._set_img_size(1066, 600)          # 模拟"最近一次截图" 1066x600

    def tearDown(self):
        server._set_img_size(*self._saved)

    def test_image_coord_scales_to_screen(self):
        with mock.patch.object(server.SERVICE, "click") as m:
            m.return_value = {"ok": True}
            server._run_single({"action": "click", "x": 600, "y": 527})
            sx, sy = m.call_args.args
            self.assertAlmostEqual(sx, 600 * server._SCREEN_W / 1066, places=3)
            self.assertAlmostEqual(sy, 527 * server._SCREEN_H / 600, places=3)

    def test_screen_coord_bypasses_scaling(self):
        with mock.patch.object(server.SERVICE, "click") as m:
            m.return_value = {"ok": True}
            server._run_single({"action": "click", "x": 600, "y": 527, "coord": "screen"})
            self.assertEqual(m.call_args.args, (600.0, 527.0))

    def test_move_and_sequence_honour_screen_coord(self):
        with mock.patch.object(server.SERVICE, "move") as m:
            m.return_value = {"ok": True}
            server._run_single({"action": "move", "x": 10, "y": 20, "coord": "screen"})
            self.assertEqual(m.call_args.args, (10.0, 20.0))
        with mock.patch.object(server.SERVICE, "click") as m:
            m.return_value = {"ok": True}
            server._run_single({"action": "click", "points": [[1, 2], [3, 4]], "coord": "screen"})
            self.assertEqual(m.call_args.kwargs["points"], [[1.0, 2.0], [3.0, 4.0]])

    def test_coord_is_echoed_in_result(self):
        with mock.patch.object(server.SERVICE, "click") as m:
            m.return_value = {"ok": True}
            r = server._run_single({"action": "click", "x": 10, "y": 20})
            self.assertEqual(r["coord"], "image")
            self.assertEqual(r["img_size"], [1066, 600])
            self.assertEqual(r["screen_size"], [server._SCREEN_W, server._SCREEN_H])

    def test_unknown_coord_raises(self):
        r = server._run_single({"action": "click", "x": 1, "y": 2, "coord": "pixel"})
        self.assertFalse(r["ok"])
        self.assertIn("coord", r["error"])

    def test_get_state_includes_coord_block(self):
        r = server.handle_tool_call("get_state", {})
        st = json.loads(r["content"][0]["text"])
        self.assertIn("coord", st)
        self.assertEqual(st["coord"]["img_size"], [1066, 600])
        self.assertEqual(st["coord"]["coord"], "image")

    def test_wait_and_keys_do_not_carry_coord_noise(self):
        with mock.patch.object(server.SERVICE, "wait") as m:
            m.return_value = None
            r = server._run_single({"action": "wait", "seconds": 0})
            self.assertNotIn("img_size", r)


class TestNewToolsDispatch(unittest.TestCase):
    def test_focus_window_forwards_selectors(self):
        with mock.patch.object(server.SERVICE, "focus_window") as m:
            m.return_value = {"ok": True, "hwnd": 11}
            r = server._run_single({"action": "focus_window", "title": "记事本"})
            self.assertTrue(r["ok"])
            self.assertEqual(m.call_args.kwargs["title"], "记事本")

    def test_send_text_parses_submit_string(self):
        with mock.patch.object(server.SERVICE, "send_text") as m:
            m.return_value = {"ok": True}
            server._run_single({"action": "send_text", "text": "hi", "submit": "ctrl+enter"})
            self.assertEqual(m.call_args.kwargs["submit_keys"], ["ctrl", "enter"])

    def test_send_text_accepts_submit_list(self):
        with mock.patch.object(server.SERVICE, "send_text") as m:
            m.return_value = {"ok": True}
            server._run_single({"action": "send_text", "text": "hi", "submit": ["enter"]})
            self.assertEqual(m.call_args.kwargs["submit_keys"], ["enter"])

    def test_tools_list_has_new_tools_with_coord_property(self):
        names = [t["name"] for t in server.TOOLS]
        self.assertIn("focus_window", names)
        self.assertIn("send_text", names)
        self.assertEqual(len(names), 26)
        for t in server.TOOLS:
            if t["name"] in ("click", "move", "drag", "zoom"):
                self.assertIn("coord", t["inputSchema"]["properties"], t["name"])


class TestVoidActionsStillReturnOk(unittest.TestCase):
    """服务层有些方法返回 None(drag/scroll/...), 分派后必须仍返回 {"ok": true} 而不是 null。"""

    def _assert_ok(self, action, method):
        with mock.patch.object(server.SERVICE, method) as m:
            m.return_value = None
            r = server._run_single(action)
            self.assertIsInstance(r, dict)
            self.assertTrue(r["ok"])
            self.assertEqual(r["coord"], "image")

    def test_drag(self):
        self._assert_ok({"action": "drag", "x1": 1, "y1": 2, "x2": 3, "y2": 4}, "drag")

    def test_scroll(self):
        self._assert_ok({"action": "scroll", "x": 1, "y": 2, "dy": -300}, "scroll")

    def test_mouse_down_and_up(self):
        self._assert_ok({"action": "mouse_down", "x": 1, "y": 2}, "mouse_down")
        self._assert_ok({"action": "mouse_up", "x": 1, "y": 2}, "mouse_up")

    def test_slide(self):
        self._assert_ok({"action": "slide", "dx": 5, "dy": 5}, "slide")

    def test_non_coord_void_action_stays_minimal(self):
        with mock.patch.object(server.SERVICE, "wait") as m:
            m.return_value = None
            r = server._run_single({"action": "wait", "seconds": 0})
            self.assertEqual(r, {"ok": True})


if __name__ == "__main__":
    unittest.main()