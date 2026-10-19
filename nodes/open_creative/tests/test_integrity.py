import numpy as np

from open_creative.product_lock.composite import composite_frame
from open_creative.product_lock.geometry import Placement
from open_creative.product_lock.integrity import CoreChecker, postencode_metrics
from open_creative.product_lock.locked import LockedProduct

W, H = 64, 48


def locked_with_core(core_value=True, x0=10):
    rgb = np.full((20, 20, 3), 120, np.uint8)
    rgb[5:15, 5:15] = (10, 200, 30)
    alpha = np.ones((20, 20), np.float32)
    core = np.zeros((20, 20), bool)
    core[4:16, 4:16] = core_value
    return LockedProduct(rgb=rgb, alpha=alpha, core=core, placement=Placement(x0, 10, 20, 20))


def bg():
    return np.random.default_rng(0).integers(0, 256, (H, W, 3), dtype=np.uint8)


def test_perfect_composite_passes():
    locked = locked_with_core()
    checker = CoreChecker(locked, W, H)
    for i in range(3):
        fc = checker.check(composite_frame(bg(), locked, None), i)
        assert fc.max_error == 0
    r = checker.report()
    assert r["core_valid"] and r["passed"]
    assert r["frames_checked"] == 3 and r["preencode_max_error"] == 0
    assert r["core_pixel_count"] == 144 and r["threshold"] == 1


def test_one_off_rounding_is_tolerated():
    locked = locked_with_core()
    checker = CoreChecker(locked, W, H)
    f = composite_frame(bg(), locked, None)
    f[20, 20, 0] += 1
    checker.check(f, 0)
    assert checker.report()["passed"]


def test_core_pixel_changed_by_two_fails():
    locked = locked_with_core()
    checker = CoreChecker(locked, W, H)
    f = composite_frame(bg(), locked, None)
    f[20, 20, 1] -= 2
    assert checker.check(f, 0).max_error == 2
    r = checker.report()
    assert not r["passed"] and r["preencode_max_error"] == 2
    assert r["failed_frames"] == [0]


def test_empty_core_is_invalid_not_passed():
    locked = locked_with_core(core_value=False)
    checker = CoreChecker(locked, W, H)
    checker.check(composite_frame(bg(), locked, None), 0)
    r = checker.report()
    assert r["core_valid"] is False and r["passed"] is False


def test_core_entirely_off_frame_is_invalid():
    locked = locked_with_core(x0=W + 5)
    checker = CoreChecker(locked, W, H)
    checker.check(bg(), 0)
    assert checker.report()["core_valid"] is False


def test_no_frames_checked_does_not_pass():
    assert CoreChecker(locked_with_core(), W, H).report()["passed"] is False


def test_postencode_metrics_on_identical_frames():
    locked = locked_with_core()
    frames = [composite_frame(bg(), locked, None) for _ in range(2)]
    m = postencode_metrics(frames, locked, W, H)
    assert m["frames"] == 2 and m["max_error"] == 0 and m["mean_abs_error"] == 0
    assert m["ssim_mean"] > 0.999
