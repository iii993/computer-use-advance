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


class TestServiceMoveAndSequence(unittest.TestCase):
    def _svc(self):
        return service.ComputerService(config={"ai": {}, "draw": {}})

    def test_move_forwards_duration(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "move") as m:
            rec = svc.move(10, 20, mode="smooth", duration_ms=500)
            m.assert_called_once()
            self.assertEqual(m.call_args.kwargs["duration_ms"], 500)
            self.assertEqual(rec["count"], 1)

    def test_move_sequence_forwards_points_and_gap(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "move") as m:
            rec = svc.move(points=[[1, 2], [3, 4]], gap_ms=150)
            self.assertEqual(m.call_args.kwargs["points"], [[1, 2], [3, 4]])
            self.assertEqual(m.call_args.kwargs["gap_ms"], 150)
            self.assertEqual(rec["gap_ms"], 150)

    def test_click_sequence_returns_record(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "click") as m:
            rec = svc.click(points=[[5, 6], [7, 8]], hold_ms=120, gap_ms=200)
            self.assertEqual(m.call_args.kwargs["points"], [[5, 6], [7, 8]])
            self.assertEqual(m.call_args.kwargs["hold_ms"], 120)
            self.assertEqual(rec["count"], 2)

    def test_click_without_coords_still_does_not_move(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "click") as m:
            svc.click(button="left")
            self.assertIsNone(m.call_args.kwargs["at"])
            self.assertIsNone(m.call_args.kwargs["points"])

    def test_click_smooth_mode_moves_before_clicking(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "move") as mm, \
             mock.patch.object(service.mouse, "click") as mc:
            svc.click(10, 20, move_mode="smooth")
            mm.assert_called_once()
            self.assertIsNone(mc.call_args.kwargs["at"])   # 已单独移动, 点击不再定位


class TestServiceValidationAfterReview(unittest.TestCase):
    """代码审查后补的边界用例。"""

    def _svc(self):
        return service.ComputerService(config={"ai": {}, "draw": {}})

    def test_gap_ms_negative_raises_on_single_point(self):
        svc = self._svc()
        with self.assertRaises(ValueError):
            svc.click(10, 20, gap_ms=-1)

    def test_only_one_of_xy_raises(self):
        svc = self._svc()
        with self.assertRaises(ValueError):
            svc.click(10, None)
        with self.assertRaises(ValueError):
            svc.click(None, 20)

    def test_points_and_xy_are_mutually_exclusive(self):
        svc = self._svc()
        with self.assertRaises(ValueError):
            svc.click(10, 20, points=[[1, 2]])
        with self.assertRaises(ValueError):
            svc.move(10, 20, points=[[1, 2]])

    def test_smooth_move_mode_forwards_duration(self):
        svc = self._svc()
        with mock.patch.object(service.mouse, "move") as mm, \
             mock.patch.object(service.mouse, "click"):
            svc.click(10, 20, move_mode="smooth", duration_ms=400)
            self.assertEqual(mm.call_args.kwargs["duration_ms"], 400)


if __name__ == "__main__":
    unittest.main()