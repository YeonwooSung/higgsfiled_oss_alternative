import io

import numpy as np
import pytest
from PIL import Image

from oc_worker.comfy.mock import MockRunner
from oc_worker.comfy.runner import SubmissionKeyReused
from open_creative.media.video import probe

BG = {"prompt": "empty dark product photography studio, purple neon", "seed": 7, "width": 360, "height": 640}


def png(path):
    return np.asarray(Image.open(path))


def test_background_image_is_deterministic(tmp_path):
    r = MockRunner(tmp_path / "mock")
    a = r.submit("product_background_image_v1", BG, "k1")
    assert r.inspect(a)["state"] == "succeeded"
    (pa,) = r.collect(a, tmp_path / "a")
    b = r.submit("product_background_image_v1", BG, "k2")
    (pb,) = r.collect(b, tmp_path / "b")
    c = r.submit("product_background_image_v1", {**BG, "seed": 8}, "k3")
    (pc,) = r.collect(c, tmp_path / "c")
    assert png(pa).shape == (640, 360, 3)
    assert np.array_equal(png(pa), png(pb))
    assert not np.array_equal(png(pa), png(pc))


def test_reused_submission_key_is_rejected_like_the_sdk(tmp_path):
    r = MockRunner(tmp_path / "mock")
    r.submit("product_background_image_v1", BG, "same")
    with pytest.raises(SubmissionKeyReused):
        r.submit("product_background_image_v1", BG, "same")
    assert r.submit_count == 1


def test_jobs_survive_runner_restart(tmp_path):
    job = MockRunner(tmp_path / "mock").submit("product_background_image_v1", BG, "k")
    again = MockRunner(tmp_path / "mock")
    assert again.inspect(job)["state"] == "succeeded"
    assert len(again.collect(job, tmp_path / "out")) == 1


def test_unknown_provider_job(tmp_path):
    with pytest.raises(KeyError):
        MockRunner(tmp_path).inspect("missing")


def test_segment_with_box_returns_object_mask(tmp_path):
    img = np.full((120, 100, 3), 235, np.uint8)
    img[30:90, 30:70] = (180, 20, 40)
    src = tmp_path / "p.png"
    Image.fromarray(img).save(src)
    r = MockRunner(tmp_path / "mock")
    job = r.submit("product_segment_v1", {"image": str(src), "prompts": {
        "points": [{"x": 0.5, "y": 0.5, "label": 1}], "box": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6}}}, "s")
    (mask_path,) = r.collect(job, tmp_path / "o")
    m = png(mask_path)
    assert m.shape == (120, 100)
    assert m[60, 50] == 255 and m[5, 5] == 0 and m[60, 20] == 0


def test_background_video_has_requested_frames(tmp_path):
    src = tmp_path / "bg.png"
    Image.fromarray(np.full((640, 360, 3), 90, np.uint8)).save(src)
    r = MockRunner(tmp_path / "mock")
    job = r.submit("product_background_video_v1", {"image": str(src), "prompt": "slow light", "seed": 1,
                                                    "width": 352, "height": 640, "frames": 25}, "v")
    (mp4,) = r.collect(job, tmp_path / "o")
    info = probe(mp4)
    assert (info["width"], info["height"], info["frame_count"]) == (352, 640, 25)


def test_capabilities_flag_mock(tmp_path):
    caps = MockRunner(tmp_path).capabilities()
    assert caps["is_mock"] is True and caps["remote_cancel"] is False
