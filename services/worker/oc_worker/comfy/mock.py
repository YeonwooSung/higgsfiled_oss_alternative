"""Mock profile: deterministic, CPU-only stand-ins for the three provider workflows.

It is not generative. Outputs are synthetic studio backdrops and an OpenCV
GrabCut mask, so the full product flow can be exercised without a GPU. The UI
labels every result of this profile as "Mock".
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from open_creative.camera_sketch.scene import encode_depth_png16, rgb_pixel_hash
from open_creative.comfy.bindings import REQUIRED_BINDINGS
from open_creative.comfy.profiles import MOCK_PROFILE
from open_creative.media.video import cover_fit, decode_frames, encode_mp4

from .runner import ProviderRejected, ProviderStatus, SubmissionKeyReused

_COLORS = {
    "black": (20, 20, 22), "white": (238, 236, 232), "light gray": (205, 203, 198), "gray": (130, 130, 130),
    "red": (180, 40, 50), "orange": (220, 130, 50), "yellow": (225, 200, 80), "green": (90, 150, 90),
    "teal": (50, 140, 140), "blue": (60, 100, 190), "purple": (130, 80, 220), "pink": (220, 120, 170),
}


def _seed_from(*parts) -> int:
    h = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).digest()
    return int.from_bytes(h[:4], "big")


def _accent_from_prompt(prompt: str) -> tuple[int, int, int]:
    found = [(prompt.find(name), name) for name in _COLORS if name in prompt]
    if not found:
        return _COLORS["light gray"]
    return _COLORS[max(found, key=lambda x: (len(x[1]), -x[0]))[1]]


def mock_background(prompt: str, seed: int, width: int, height: int) -> np.ndarray:
    rng = np.random.default_rng(_seed_from(prompt, seed))
    accent = np.array(_accent_from_prompt(prompt), np.float32)
    dark = "dark" in prompt
    base = accent * (0.25 if dark else 0.85) + (0 if dark else 0.15 * 255)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    horizon = height * rng.uniform(0.58, 0.68)
    light_x = width * rng.uniform(0.2, 0.8)
    wall = 1.0 - 0.35 * (yy / horizon) ** 1.5
    glow = np.exp(-(((xx - light_x) / (0.6 * width)) ** 2 + ((yy - 0.3 * height) / (0.5 * height)) ** 2))
    shade = np.where(yy < horizon, wall * (0.8 + 0.3 * glow), 0.75 + 0.15 * (yy - horizon) / (height - horizon))
    img = base[None, None, :] * shade[..., None]
    if dark:
        for y in rng.uniform(0.12, 0.45, 2) * height:
            band = np.exp(-(((yy - y) / (height * 0.004)) ** 2))
            img += band[..., None] * accent * 0.9
    noise = rng.normal(0, 1.5, img.shape[:2])[..., None]
    return np.clip(np.rint(img + noise), 0, 255).astype(np.uint8)


def mock_video_frames(still: np.ndarray, seed: int, width: int, height: int, frames: int) -> Iterator[np.ndarray]:
    base = cover_fit(still, width, height).astype(np.float32)
    rng = np.random.default_rng(seed)
    phase = rng.uniform(0, 2 * np.pi)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    for t in range(frames):
        cx = width * (0.3 + 0.4 * (0.5 + 0.5 * np.sin(phase + 2 * np.pi * t / max(frames, 1))))
        spot = np.exp(-(((xx - cx) / (0.35 * width)) ** 2 + ((yy - 0.3 * height) / (0.3 * height)) ** 2))
        gain = 1.0 + 0.04 * np.sin(phase + 2 * np.pi * t / max(frames, 1)) + 0.08 * spot
        yield np.clip(np.rint(base * gain[..., None]), 0, 255).astype(np.uint8)


def mock_segment(rgb: np.ndarray, prompts: dict) -> np.ndarray:
    h, w = rgb.shape[:2]
    box = prompts.get("box")
    if box:
        rect = (int(box["x"] * w), int(box["y"] * h), max(2, int(box["w"] * w)), max(2, int(box["h"] * h)))
    else:
        mx, my = max(1, w // 20), max(1, h // 20)
        rect = (mx, my, w - 2 * mx, h - 2 * my)
    mask = np.full((h, w), cv2.GC_BGD, np.uint8)
    x, y, rw, rh = rect
    mask[y : y + rh, x : x + rw] = cv2.GC_PR_FGD
    for p in prompts.get("points", []):
        px, py = min(w - 1, int(p["x"] * w)), min(h - 1, int(p["y"] * h))
        cv2.circle(mask, (px, py), max(2, min(w, h) // 50), cv2.GC_FGD if p.get("label", 1) == 1 else cv2.GC_BGD, -1)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), mask, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
    return np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)


def _camera_fixtures_dir() -> Path:
    return Path(os.environ.get("OC_CAMERA_FIXTURES") or Path(__file__).resolve().parents[4] / "fixtures" / "camera-sketch")


def _fixture_depth(image_path: Path, rgb: np.ndarray) -> tuple[bytes, dict] | None:
    """Ground-truth (png16, meta) when the input is a Camera Sketch fixture: matched by file sha256, or
    (the upload may have been re-encoded by normalization) by decoded pixel hash."""
    d = _camera_fixtures_dir()
    try:
        scenes = json.loads((d / "scenes.json").read_text())["scenes"]
    except (OSError, ValueError, KeyError):
        return None
    file_hash = hashlib.sha256(Path(image_path).read_bytes()).hexdigest()
    pix_hash = rgb_pixel_hash(rgb)
    for sc in scenes.values():
        if sc.get("sha256") == file_hash or sc.get("rgb_sha256") == pix_hash:
            return (d / sc["depth"]).read_bytes(), {"min": sc["depth_png16"]["min"], "max": sc["depth_png16"]["max"]}
    return None


def mock_depth(rgb: np.ndarray) -> np.ndarray:
    """Mock inverse depth (float32, larger = nearer) for an unknown image: 0.6 * vertical gradient
    (bottom nearer) + 0.4 * blurred luminance. Deterministic; not a depth estimate."""
    h, w = rgb.shape[:2]
    grad = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None] * np.ones((1, w), np.float32)
    lum = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    sigma = max(1.0, min(h, w) / 40.0)
    lum = cv2.GaussianBlur(lum, (0, 0), sigma)
    return (0.6 * grad + 0.4 * lum).astype(np.float32)


def mock_cinematic_frame(guide: np.ndarray) -> np.ndarray:
    """Mock cinematic stage: holes (near-black guide pixels) inpainted with cv2 TELEA, then a mild
    deterministic warm grade (slight tint + contrast)."""
    gray = guide.max(axis=2)
    holes = (gray <= 6).astype(np.uint8) * 255
    out = guide
    if holes.any():
        holes = cv2.dilate(holes, np.ones((3, 3), np.uint8))
        out = cv2.cvtColor(cv2.inpaint(cv2.cvtColor(guide, cv2.COLOR_RGB2BGR), holes, 3, cv2.INPAINT_TELEA),
                           cv2.COLOR_BGR2RGB)
    f = (out.astype(np.float32) - 128.0) * 1.06 + 128.0
    f *= np.array([1.03, 1.0, 0.96], np.float32)
    return np.clip(np.rint(f), 0, 255).astype(np.uint8)


def _guide_frames(src: str) -> list[np.ndarray]:
    p = Path(src)
    if p.is_dir():
        files = sorted(x for x in p.iterdir() if x.suffix.lower() in (".png", ".jpg", ".jpeg"))
        return [np.asarray(Image.open(x).convert("RGB")) for x in files]
    return list(decode_frames(p))


class MockRunner:
    profile = MOCK_PROFILE

    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "keys").mkdir(parents=True, exist_ok=True)
        self.submit_count = 0

    def capabilities(self) -> dict:
        return {"profile": self.profile, "is_mock": True, "remote_cancel": False,
                "workflows": sorted(REQUIRED_BINDINGS)}

    def _job_dir(self, job_id: str) -> Path:
        d = self.root / job_id
        if not (d / "meta.json").exists():
            raise KeyError(job_id)
        return d

    def submit(self, workflow: str, bindings: dict, submission_key: str) -> str:
        if workflow not in REQUIRED_BINDINGS:
            raise ProviderRejected(f"unknown workflow {workflow}")
        missing = set(REQUIRED_BINDINGS[workflow]) - set(bindings)
        if missing:
            raise ProviderRejected(f"missing bindings {sorted(missing)}")
        key_file = self.root / "keys" / hashlib.sha256(submission_key.encode()).hexdigest()
        if key_file.exists():
            raise SubmissionKeyReused(submission_key)
        job_id = uuid.uuid4().hex
        d = self.root / job_id
        d.mkdir(parents=True)
        key_file.write_text(job_id)
        self.submit_count += 1
        try:
            outputs = self._execute(workflow, bindings, d)
            meta = {"workflow": workflow, "state": "succeeded", "outputs": outputs, "error": None}
        except Exception as e:  # noqa: BLE001 - recorded as provider failure
            meta = {"workflow": workflow, "state": "failed", "outputs": [], "error": str(e)}
        (d / "meta.json").write_text(json.dumps(meta))
        return job_id

    def _execute(self, workflow: str, b: dict, d: Path) -> list[str]:
        if workflow == "product_background_image_v1":
            img = mock_background(b["prompt"], b["seed"], b["width"], b["height"])
            Image.fromarray(img).save(d / "image_0.png")
            return ["image_0.png"]
        if workflow == "product_background_video_v1":
            still = np.asarray(Image.open(b["image"]).convert("RGB"))
            frames = mock_video_frames(still, b["seed"], b["width"], b["height"], b["frames"])
            encode_mp4(frames, d / "video_0.mp4", b["width"], b["height"], fps=24)
            return ["video_0.mp4"]
        if workflow == "product_segment_v1":
            rgb = np.asarray(Image.open(b["image"]).convert("RGB"))
            Image.fromarray(mock_segment(rgb, b["prompts"]), "L").save(d / "mask_0.png")
            return ["mask_0.png"]
        if workflow == "camera_depth_v1":
            rgb = np.asarray(Image.open(b["image"]).convert("RGB"))
            fx = _fixture_depth(Path(b["image"]), rgb)
            if fx is not None:
                data, dm = fx
                source = "fixture_gt"
            else:
                data, dm = encode_depth_png16(mock_depth(rgb))
                source = "heuristic"
            (d / "depth_0.png").write_bytes(data)
            (d / "depth_0.json").write_text(json.dumps({
                "min": dm["min"], "max": dm["max"], "convention": "inverse_relative", "png16": "linear min..max -> 0..65535",
                "mock": True, "source": source, "model": "Mock (not Depth Anything)"}))
            return ["depth_0.png", "depth_0.json"]
        if workflow == "camera_cinematic_v1":
            w, h, n = b["width"], b["height"], b["frames"]
            src = _guide_frames(b["guide_rgb"])
            if not src:
                raise ProviderRejected("guide_rgb has no frames")
            frames = (mock_cinematic_frame(cover_fit(src[min(i, len(src) - 1)], w, h)) for i in range(n))
            encode_mp4(frames, d / "video_0.mp4", w, h, fps=24)
            (d / "video_0.json").write_text(json.dumps({
                "mock": True, "frames": n, "width": w, "height": h, "label": "Mock",
                "note": "geometry frames with holes inpainted + mild grade; not generative"}))
            return ["video_0.mp4", "video_0.json"]
        raise ProviderRejected(workflow)

    def inspect(self, provider_job_id: str) -> ProviderStatus:
        meta = json.loads((self._job_dir(provider_job_id) / "meta.json").read_text())
        return {"state": meta["state"], "progress": 1.0 if meta["state"] == "succeeded" else None,
                "error": meta["error"]}

    def events(self, provider_job_id: str, cursor: str | None = None) -> Iterator[dict]:
        yield {"type": "status", "state": self.inspect(provider_job_id)["state"]}

    def collect(self, provider_job_id: str, target_dir: Path, workflow: str | None = None) -> list[Path]:
        d = self._job_dir(provider_job_id)
        meta = json.loads((d / "meta.json").read_text())
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        return [Path(shutil.copy(d / name, target_dir / name)) for name in meta["outputs"]]

    def cancel(self, provider_job_id: str) -> ProviderStatus:
        # Mock jobs complete synchronously; there is nothing to cancel remotely.
        return self.inspect(provider_job_id)
