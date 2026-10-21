import io
import json
import zipfile

import cv2
import numpy as np
import pytest
from open_creative.pocket_vfx.masks import (
    MaskFormatError,
    MaskSequence,
    apply_mask_edits,
    build_prompts,
    contour_band,
    contour_band_patch,
    decode_prompt_mask,
    edit_to_proxy,
    edits_as_prompts,
    manifest,
    mask_diagnostics,
    mask_stats,
    prompts_hash,
    read_masks_zip,
    smooth_masks,
    upscale_mask,
    write_masks_zip,
)
from PIL import Image

PW, PH = 64, 36


def disk(cx, cy, r, w=PW, h=PH):
    m = np.zeros((h, w), np.uint8)
    cv2.circle(m, (cx, cy), r, 255, -1)
    return m


def seq_of(masks, **kw):
    return MaskSequence(masks=list(masks), prompts_hash=kw.get("prompts_hash", "p" * 8), model=kw.get("model", "t"))


# ---- stats, manifest, zip -------------------------------------------------------------------------------------


def test_mask_stats_area_holes_empty():
    m = np.zeros((40, 60), np.uint8)
    m[5:35, 5:55] = 255
    for i in range(5):  # 5 holes of 3x3 px
        m[15:18, 8 + 9 * i : 11 + 9 * i] = 0
    s = mask_stats(m)
    assert s == {"area": 30 * 50 - 5 * 9, "holes": 5, "empty": False}
    assert mask_stats(np.zeros((4, 4), np.uint8)) == {"area": 0, "holes": 0, "empty": True}


def test_zip_round_trip_with_manifest(tmp_path):
    seq = seq_of([disk(20, 18, 6), disk(22, 18, 6), np.zeros((PH, PW), np.uint8)], model="Mock")
    path = tmp_path / "m.zip"
    doc = write_masks_zip(seq, path)
    assert doc == manifest(seq)
    assert doc["schema_version"] == 1 and doc["size"] == [PW, PH] and doc["model"] == "Mock"
    assert [f["file"] for f in doc["frames"]] == ["mask_0000.png", "mask_0001.png", "mask_0002.png"]
    assert doc["frames"][2]["empty"] is True and doc["frames"][0]["area"] == int((seq.masks[0] > 127).sum())
    with zipfile.ZipFile(path) as z:
        assert json.loads(z.read("manifest.json")) == doc
        png = Image.open(io.BytesIO(z.read("mask_0001.png")))
        assert png.mode == "L" and png.size == (PW, PH)  # 8-bit gray
    back = read_masks_zip(path, expect_frames=3, expect_size=(PW, PH))
    assert all(np.array_equal(a, b) for a, b in zip(back.masks, seq.masks, strict=True))
    assert back.model == "Mock" and back.prompts_hash == seq.prompts_hash


def test_read_zip_without_manifest_and_contract_errors(tmp_path):
    path = tmp_path / "raw.zip"
    with zipfile.ZipFile(path, "w") as z:
        for i in range(2):
            buf = io.BytesIO()
            Image.fromarray(disk(10 + i, 10, 4)).save(buf, format="PNG")
            z.writestr(f"mask_{i:04d}.png", buf.getvalue())
    assert len(read_masks_zip(path)) == 2
    with pytest.raises(MaskFormatError, match="frames"):
        read_masks_zip(path, expect_frames=3)
    with pytest.raises(MaskFormatError, match="size"):
        read_masks_zip(path, expect_size=(32, 18))


# ---- resize ---------------------------------------------------------------------------------------------------


def test_upscale_soft_and_binary():
    m = np.zeros((36, 64), np.uint8)
    m[10:20, 30:40] = 255
    soft = upscale_mask(m, 128, 72)
    assert soft.shape == (72, 128) and soft.dtype == np.float32 and 0 <= soft.min() and soft.max() <= 1
    assert soft[30, 70] == pytest.approx(1.0) and soft[5, 5] == 0.0
    assert 0 < soft[20, 70] < 1  # bilinear edge
    hard = upscale_mask(m, 128, 72, soft=False)
    assert set(np.unique(hard)) == {0.0, 1.0}
    assert hard[20:40, 60:80].all() and not hard[19, 70] and not hard[40, 70]  # nearest: exact 2x block
    # the soft path never shifts the silhouette: centroid stays put (no half-pixel drift)
    ys, xs = np.nonzero(soft > 0.5)
    assert xs.mean() == pytest.approx(69.5, abs=0.6) and ys.mean() == pytest.approx(29.5, abs=0.6)


def test_edit_to_proxy_any_size():
    edit = np.zeros((720, 1280), np.uint8)
    edit[200:400, 600:800] = 255
    p = edit_to_proxy(edit, 640, 360)
    assert p.shape == (360, 640) and p.dtype == np.uint8 and set(np.unique(p)) == {0, 255}
    assert p[150, 350] == 255 and p[90, 350] == 0


# ---- contour band ---------------------------------------------------------------------------------------------


def test_contour_band_is_a_ring_around_the_contour():
    m = np.zeros((200, 300), np.float32)
    cv2.circle(m, (150, 100), 50, 1.0, -1)
    band = contour_band(m, 3, 8)
    assert band.shape == m.shape and band.dtype == np.float32
    assert band[100, 150] == 0  # deep inside
    assert band[100, 150 + 50] > 0.5 and band[100, 150 + 53] > 0.3  # on and just outside the contour
    assert band[100, 150 + 80] == 0  # far outside
    box, crop = contour_band_patch(m, 3, 8)
    x0, y0, x1, y1 = box
    assert np.array_equal(band[y0:y1, x0:x1], crop) and band.sum() == pytest.approx(crop.sum())
    assert contour_band_patch(np.zeros((10, 10), np.float32), 1, 2) is None
    assert not contour_band(np.zeros((10, 10), np.float32), 1, 2).any()


def test_smooth_masks_temporal_majority():
    a, b = disk(20, 18, 8), disk(20, 18, 8)
    blip = np.zeros_like(a)  # one dropped frame in the middle
    out = smooth_masks([a, blip, b], radius=1)
    assert len(out) == 3 and out[1].dtype == np.uint8
    assert (out[1] > 127).sum() == (a > 127).sum()  # 2 of 3 frames agree -> restored
    assert np.array_equal(out[0] > 127, a > 127)  # window clipped at the ends


# ---- diagnostics ----------------------------------------------------------------------------------------------


def test_area_jump_flagged_when_a_frame_drops_to_half_area():
    full = np.zeros((PH, PW), np.uint8)
    full[6:30, 10:50] = 255
    half = full.copy()
    half[6:30, 30:50] = 0
    diags = mask_diagnostics(seq_of([full, full, half, full, full]))
    assert {"frame": 2, "code": "area_jump"} in diags and {"frame": 3, "code": "area_jump"} in diags
    assert not [d for d in diags if d["frame"] in (0, 1, 4)]
    small = full.copy()
    small[6:30, 46:50] = 0  # -10 %: normal motion
    assert mask_diagnostics(seq_of([full, small, full])) == []


def test_empty_and_holes_diagnostics():
    full = np.zeros((PH, PW), np.uint8)
    full[4:32, 4:60] = 255
    holey = full.copy()
    for i in range(4):
        holey[10:13, 8 + 10 * i : 11 + 10 * i] = 0
    empty = np.zeros_like(full)
    diags = mask_diagnostics(seq_of([full, holey, full, empty]))
    assert {"frame": 1, "code": "holes"} in diags and {"frame": 3, "code": "mask_empty"} in diags
    # an empty frame is only a problem where the prompts expect the subject (the area change still counts)
    assert mask_diagnostics(seq_of([full, empty]), expects_subject={0}) == [{"frame": 1, "code": "area_jump"}]


# ---- mask edits as prompts ------------------------------------------------------------------------------------


def test_apply_mask_edits_replaces_frames_and_becomes_prompt():
    base = seq_of([disk(20, 18, 6)] * 4)
    edit = disk(40, 18, 9)
    out = apply_mask_edits(base, {2: edit})
    assert np.array_equal(out.masks[2], edit) and np.array_equal(out.masks[1], base.masks[1])
    assert base.masks[2] is not out.masks[2]  # the input sequence is not modified
    prompts = edits_as_prompts({2: edit})
    assert prompts[0]["frame"] == 2
    assert np.array_equal(decode_prompt_mask(prompts[0]["png_b64"], PW, PH), edit)
    with pytest.raises(ValueError):
        apply_mask_edits(base, {7: edit})
    with pytest.raises(ValueError):
        apply_mask_edits(base, {1: np.zeros((10, 10), np.uint8)})


def test_build_prompts_in_proxy_px_and_hash_ignores_mock_context():
    sp = [{"frame": 43, "points": [{"x": 0.5, "y": 0.25, "label": 1}, {"x": 0.1, "y": 0.9, "label": 0}]}]
    p = build_prompts(sp, {50: disk(10, 10, 4)}, PW, PH, 120)
    assert p["width"] == PW and p["height"] == PH and p["frame_count"] == 120 and p["obj_id"] == 1
    assert p["point_frames"] == [{"frame": 43, "points": [{"x": 32.0, "y": 9.0, "label": 1},
                                                          {"x": 6.4, "y": 32.4, "label": 0}]}]
    assert [m["frame"] for m in p["mask_frames"]] == [50]
    with_ctx = build_prompts(sp, {50: disk(10, 10, 4)}, PW, PH, 120,
                             mock_context={"source_sha256": "a" * 64, "source_pts_ms": [0.0]})
    assert with_ctx["mock_context"]["source_sha256"] == "a" * 64
    assert prompts_hash(with_ctx) == prompts_hash(p)
    assert prompts_hash(build_prompts(sp, {}, PW, PH, 120)) != prompts_hash(p)
