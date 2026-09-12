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
