# tests/test_mcp_dispatch.py
import sys, os, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))

from computer_mcp import server


class TestClickDispatch(unittest.TestCase):
    """审查修复: 省略坐标必须"原地点击", 不能默认落到 (0,0)。"""

    def test_click_without_coords_clicks_in_place(self):
        with mock.patch.object(server.SERVICE, "click") as m:
            server._run_single({"action": "click"})
            self.assertEqual(m.call_args.args, ())          # 不传位置坐标
            self.assertNotIn("at", m.call_args.kwargs)

    def test_click_with_coords_passes_screen_coords(self):
        with mock.patch.object(server.SERVICE, "click") as m:
            server._run_single({"action": "click", "x": 100, "y": 200, "hold_ms": 50})
            self.assertEqual(len(m.call_args.args), 2)
            self.assertEqual(m.call_args.kwargs["hold_ms"], 50)

    def test_click_sequence_forwards_points(self):
        with mock.patch.object(server.SERVICE, "click") as m:
            server._run_single({"action": "click", "points": [[1, 2], [3, 4]], "gap_ms": 20})
            self.assertEqual(len(m.call_args.kwargs["points"]), 2)
            self.assertEqual(m.call_args.kwargs["gap_ms"], 20)


if __name__ == "__main__":
    unittest.main()
