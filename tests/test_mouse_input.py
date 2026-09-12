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
            mt.assert_not_called()

    def test_click_at_positioning_keeps_legacy_instant_behavior(self):
        with mock.patch.object(mouse, "mc") as m, mock.patch.object(mouse, "move_to") as mt:
            mouse.click(at=(100, 200))
            mt.assert_not_called()               # 旧行为是瞬移, 不是平滑移动
            self.assertEqual(m.click.call_count, 1)

    def test_legacy_positional_xy_still_works(self):
        # 旧调用 mouse.click("right", 100, 200) 必须保持原语义
        with mock.patch.object(mouse, "mc") as m:
            mouse.click("right", 100, 200)
            m.click.assert_called_once()
            self.assertEqual(m.position, (100, 200))


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
