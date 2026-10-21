"""Generate the synthetic Pocket VFX clips (MIT, procedurally rendered, no third-party footage).

Run: uv run python fixtures/pocket-vfx/make_fixtures.py
     uv run python fixtures/pocket-vfx/make_fixtures.py --plates   (bundled effect plates only)
     uv run python fixtures/pocket-vfx/make_fixtures.py --sfx      (bundled SFX WAVs + preset peak_offset_ms)

A seeded textured ground + a body-shaped blob that stomps across the frame while one arm swings a
distinct yellow "hand" disk. Scene content is a pure function of time, so ground truth is exact.
Writes (all silent unless noted):
  stomp_landscape.mp4   1280x720, 24 fps CFR, 5 s; GT hand trajectory per frame -> tracks.json,
                        GT binary subject masks -> stomp_masks.zip (mask_0000.png..., 1-bit PNG)
  occluded_hand.mp4     same scene, a dark bar covers the hand on frames 50..62 (inclusive)
  rotated_portrait.mp4  720x1280 portrait scene stored as a 1280x720 stream + display matrix
                        (ffprobe rotation -90 = player rotates 90 deg clockwise, like a phone), 30 fps;
                        GT subject masks in DISPLAY orientation per source frame -> portrait_masks.zip
  vfr.mp4               1280x720, mixed 30/15 fps timestamps (each 2 s block: 1 s @30, 1 s @15), 6 s
  frame_numbers.mp4     640x360, same VFR timing, source frame index burned in as a 9-bit block code
  with_audio.mp4        640x360, 24 fps, 5 s + AAC 440 Hz tone whose stream starts at +200 ms
  clean_plate.png       the empty background of stomp_landscape
  manifest.json         sha256 of every file + GT (display-space hand trajectories per source PTS
                        for rotated_portrait and vfr, frame_numbers PTS + code layout, audio onset)
stomp_landscape.mp4 and rotated_portrait.mp4 are copied to apps/pocket-vfx/public/samples/.

--plates writes the bundled Quick-render plates named in presets/pocket-vfx/presets.v1.json (MIT, procedural
emissive gray on black, ``open_creative.pocket_vfx.plate.procedural_plate``): <id>.mp4 768x448 and
<id>_portrait.mp4 448x768, 49 frames @24 fps, periodic so the loop seam passes ``loop_seam_ok``, plus
presets/pocket-vfx/plates/manifest.json (sha256, seed, style, license).
--sfx writes presets/pocket-vfx/sfx/{impact,aura,blink}_v1.wav (MIT, synthesized, 48 kHz mono 16-bit) and sets each
preset's ``sfx.peak_offset_ms`` to the measured energy peak of its file (repo presets and the packaged mirror).
Fixed seeds and bitexact muxing -> byte-identical outputs on the same FFmpeg/x264 build.
"""

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import wave
import zipfile
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from open_creative.media.video_ingest import _frame_pts_ms
from open_creative.pocket_vfx.audio import RATE, energy_peak_ms
from open_creative.pocket_vfx.matte import loop_seam_ok
from open_creative.pocket_vfx.plate import (
    BUNDLED_FRAMES,
    BUNDLED_SIZES,
    procedural_plate,
)
from open_creative.pocket_vfx.presets import load_presets, presets_path
from PIL import Image

OUT = Path(__file__).parent
SAMPLES = OUT.parents[1] / "apps" / "pocket-vfx" / "public" / "samples"
HAND_RGB = (255, 214, 0)
BITEXACT = ["-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1"]
CRF = "22"


# ---------------------------------------------------------------------------- scene


def background(w, h, seed):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    horizon = 0.55 * h
    wall = np.clip(np.asarray((120, 132, 150), np.float32) * (0.85 + 0.15 * y / h)[..., None], 0, 255)
    wall *= (0.92 + 0.08 * (np.floor(x / (0.05 * min(w, h))) % 2))[..., None]
    gy = np.maximum(y - horizon, 1.0)
    plank = np.floor((x - w / 2) / (0.02 * min(w, h) + 0.5 * gy)) % 2
    rows = np.floor(np.log(gy) * 6.0) % 2
    ground = np.asarray((150, 118, 86), np.float32) * (0.80 + 0.12 * plank + 0.08 * rows)[..., None]
    img = np.where((y > horizon)[..., None], ground, wall)
    tex = cv2.GaussianBlur(rng.normal(0.0, 14.0, (h, w)).astype(np.float32), (0, 0), 1.2)
    return np.clip(img + tex[..., None], 0, 255).astype(np.uint8)


def pose(t, w, h):
    """Body + hand geometry at time t (seconds), in display pixels."""
    u = min(w, h)
    ground = 0.82 * h
    cx = w * (0.35 + 0.25 * t / 5.0)
    lift = 0.05 * u * abs(math.sin(math.pi * t / 0.8))  # stomp: rise and slam every 0.8 s
    hip = ground - 0.28 * u - lift
    shoulder = (cx + 0.04 * u, hip - 0.22 * u)
    a = -0.4 + 0.9 * math.sin(2 * math.pi * t / 2.2)
    hand = (shoulder[0] + 0.2 * u * math.cos(a), shoulder[1] + 0.2 * u * math.sin(a))
    return {"u": u, "ground": ground, "cx": cx, "lift": lift, "hip": hip, "shoulder": shoulder, "hand": hand}


def _p(xy):
    return (round(xy[0] * 16), round(xy[1] * 16))  # cv2 fixed point, shift=4


def draw(bg, t):
    """Frame (RGB uint8) and binary subject mask (uint8 0/1) at time t."""
    h, w = bg.shape[:2]
    p = pose(t, w, h)
    u, cx, hip = p["u"], p["cx"], p["hip"]
    img = bg.copy()
    mask = np.zeros((h, w), np.uint8)
    feet = [(cx - 0.06 * u, p["ground"] - 0.6 * p["lift"]), (cx + 0.06 * u, p["ground"])]
    parts = [
        ("line", ((cx - 0.03 * u, hip), feet[0]), int(0.035 * u), (52, 60, 110)),
        ("line", ((cx + 0.03 * u, hip), feet[1]), int(0.035 * u), (52, 60, 110)),
        ("ellipse", ((cx, hip - 0.12 * u), (0.07 * u, 0.13 * u)), 0, (64, 84, 170)),
        ("circle", ((cx, hip - 0.31 * u), 0.055 * u), 0, (220, 170, 140)),
        ("line", (p["shoulder"], p["hand"]), int(0.025 * u), (64, 84, 170)),
        ("circle", (p["hand"], 0.022 * u), 0, HAND_RGB),
    ]
    for kind, geo, thick, color in parts:
        for target, value in ((img, color), (mask, 1)):
            if kind == "line":
                cv2.line(target, _p(geo[0]), _p(geo[1]), value, thick, cv2.LINE_8, 4)
            elif kind == "ellipse":
                axes = (round(geo[1][0] * 16), round(geo[1][1] * 16))
                cv2.ellipse(target, _p(geo[0]), axes, 0, 0, 360, value, -1, cv2.LINE_8, 4)
            else:
                cv2.circle(target, _p(geo[0]), round(geo[1] * 16), value, -1, cv2.LINE_8, 4)
    return img, mask, p["hand"]


# ---------------------------------------------------------------------------- encoding


def encode(frames, path, w, h, rate=24, setpts=None, audio=None, crf=CRF):
    """rawvideo rgb24 pipe -> H.264 yuv420p MP4. ``setpts`` (fixed expression, input timebase
    1/rate) produces VFR timestamps; ``audio`` = extra lavfi input args."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(rate), "-i", "pipe:0"]
    if audio:
        cmd += audio
    cmd += ["-map", "0:v"] + (["-map", "1:a", "-c:a", "aac", "-b:a", "96k"] if audio else ["-an"])
    if setpts:
        cmd += ["-vf", f"setpts='{setpts}'", "-fps_mode", "passthrough"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", crf, "-preset", "medium", "-threads", "1",
            *BITEXACT, "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        proc.stdin.write(np.ascontiguousarray(f).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"encode failed: {path}")


# Each 2 s block = 30 frames @30 fps then 15 frames @15 fps (60 ticks of 1/30 s).
VFR_SETPTS = "60*floor(N/45)+if(lt(mod(N,45),30),mod(N,45),30+2*(mod(N,45)-30))"
VFR_FRAMES = 135  # 3 blocks = 6 s


def vfr_time(n):
    b, r = divmod(n, 45)
    return (60 * b + (r if r < 30 else 30 + 2 * (r - 30))) / 30.0


def hand_records(path, times, positions, w, h):
    pts = _frame_pts_ms(path)
    assert len(pts) == len(times), (path, len(pts), len(times))
    return [{"frame": k, "pts_ms": round(pts[k], 6), "t_ms": round(t * 1000, 6),
             "x": round(x, 3), "y": round(y, 3), "u": round(x / w, 6), "v": round(y / h, 6)}
            for k, (t, (x, y)) in enumerate(zip(times, positions))]


# ---------------------------------------------------------------------------- clips


def write_masks(path, masks):
    """GT masks (uint8 0/1) -> zip of mask_%04d.png, 1-bit PNG, 255 = subject."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for f, m in enumerate(masks):
            buf = BytesIO()
            Image.fromarray(m * 255).convert("1").save(buf, format="PNG", optimize=True)
            info = zipfile.ZipInfo(f"mask_{f:04d}.png", date_time=(1980, 1, 1, 0, 0, 0))
            z.writestr(info, buf.getvalue())


def portrait_masks(bg, times):
    """GT masks of rotated_portrait, display orientation (720x1280), one per source frame."""
    write_masks(OUT / "portrait_masks.zip", [draw(bg, t)[1] for t in times])


def stomp_clips():
    w, h, fps, n = 1280, 720, 24, 120
    bg = background(w, h, 7)
    occluded = range(50, 63)
    track, masks = [], []

    def frames(with_bar):
        for f in range(n):
            img, mask, (hx, hy) = draw(bg, f / fps)
            if not with_bar:
                track.append({"frame": f, "t_ms": round(f * 1000 / fps, 6), "x": round(hx, 3), "y": round(hy, 3),
                              "u": round(hx / w, 6), "v": round(hy / h, 6), "visible_in_occluded": f not in occluded})
                masks.append(mask)
            elif f in occluded:
                bx = hx + (f - 56) * 6  # the bar sweeps left->right across the hand
                cv2.rectangle(img, (int(bx - 50), 0), (int(bx + 50), h - 1), (30, 30, 34), -1)
            yield img

    encode(frames(False), OUT / "stomp_landscape.mp4", w, h, fps)
    encode(frames(True), OUT / "occluded_hand.mp4", w, h, fps)
    Image.fromarray(bg, "RGB").save(OUT / "clean_plate.png", optimize=True)
    (OUT / "tracks.json").write_text(json.dumps(
        {"clip": "stomp_landscape.mp4", "also": "occluded_hand.mp4", "space": "display px of 1280x720",
         "fps": fps, "hand": track}, indent=1) + "\n")
    write_masks(OUT / "stomp_masks.zip", masks)
    return {"fps": fps, "frames": n, "size": [w, h], "tracks": "tracks.json", "masks": "stomp_masks.zip",
            "masks_format": "zip of mask_%04d.png, 1-bit, 255 = subject (body + arm + hand), per output frame",
            "hand_radius_px": round(0.022 * 720, 3), "occluded_hand": {"frames": [occluded[0], occluded[-1]],
                                                                     "inclusive": True}}


def rotated_portrait():
    dw, dh, fps, n = 720, 1280, 30, 120
    bg = background(dw, dh, 11)
    times = [k / fps for k in range(n)]
    positions = []

    def frames():
        for t in times:
            img, _, hand = draw(bg, t)
            positions.append(hand)
            yield np.rot90(img, k=1)  # store counter-clockwise; the player rotates back clockwise

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.mp4"
        encode(frames(), raw, dh, dw, fps)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-display_rotation", "-90",
                        "-i", str(raw), "-c", "copy", *BITEXACT, "-movflags", "+faststart",
                        str(OUT / "rotated_portrait.mp4")], check=True)
    portrait_masks(bg, times)
    return {"stored_size": [dh, dw], "display_size": [dw, dh], "rotation_cw": 90, "fps": fps, "frames": n,
            "masks": "portrait_masks.zip",
            "masks_format": "zip of mask_%04d.png, 1-bit, 255 = subject, display orientation, per source frame",
            "hand_display_px": hand_records(OUT / "rotated_portrait.mp4", times, positions, dw, dh)}


def vfr():
    w, h = 1280, 720
    bg = background(w, h, 13)
    times = [vfr_time(k) for k in range(VFR_FRAMES)]
    positions = []

    def frames():
        for t in times:
            img, _, hand = draw(bg, t)
            positions.append(hand)
            yield img

    encode(frames(), OUT / "vfr.mp4", w, h, 30, setpts=VFR_SETPTS)
    return {"pattern": "per 2 s: 1 s @30 fps then 1 s @15 fps", "frames": VFR_FRAMES, "size": [w, h],
            "hand_display_px": hand_records(OUT / "vfr.mp4", times, positions, w, h)}


CODE = {"bits": 9, "block": 64, "x0": 32, "y0": 148, "channel": "any (gray)", "one": 230, "zero": 25,
        "bit_order": "LSB first, left to right"}


def frame_numbers():
    w, h = 640, 360

    def frames():
        for k in range(VFR_FRAMES):
            img = np.full((h, w, 3), 128, np.uint8)
            for b in range(CODE["bits"]):
                x0 = CODE["x0"] + b * CODE["block"]
                v = CODE["one"] if (k >> b) & 1 else CODE["zero"]
                img[CODE["y0"] : CODE["y0"] + CODE["block"], x0 : x0 + CODE["block"]] = v
            yield img

    encode(frames(), OUT / "frame_numbers.mp4", w, h, 30, setpts=VFR_SETPTS, crf="18")
    return {"frames": VFR_FRAMES, "size": [w, h], "timing": "same as vfr.mp4",
            "pts_ms": [round(p, 6) for p in _frame_pts_ms(OUT / "frame_numbers.mp4")], "code_layout": CODE}


def with_audio():
    w, h, fps, n = 640, 360, 24, 120
    bg = background(w, h, 17)
    audio = ["-itsoffset", "0.2", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4.8"]
    encode((draw(bg, f / fps)[0] for f in range(n)), OUT / "with_audio.mp4", w, h, fps, audio=audio)
    return {"tone_hz": 440, "tone_amplitude": 0.125, "tone_onset_ms": 200, "tone_duration_ms": 4800,
            "note": "audio stream is offset +200 ms from the first video frame (stream start_time "
                    "includes AAC priming, ~178 ms)"}


def main():
    SAMPLES.mkdir(parents=True, exist_ok=True)
    meta = {"license": "MIT (procedurally generated)", "hand_color_rgb": list(HAND_RGB),
            "stomp_landscape": stomp_clips(), "rotated_portrait": rotated_portrait(), "vfr": vfr(),
            "frame_numbers": frame_numbers(), "with_audio": with_audio()}
    names = ["stomp_landscape.mp4", "occluded_hand.mp4", "rotated_portrait.mp4", "vfr.mp4", "frame_numbers.mp4",
             "with_audio.mp4", "clean_plate.png", "tracks.json", "stomp_masks.zip", "portrait_masks.zip"]
    meta["files"] = {n: {"sha256": hashlib.sha256((OUT / n).read_bytes()).hexdigest(),
                         "bytes": (OUT / n).stat().st_size} for n in names}
    (OUT / "manifest.json").write_text(json.dumps(meta, indent=1) + "\n")
    for n in ("stomp_landscape.mp4", "rotated_portrait.mp4"):
        shutil.copyfile(OUT / n, SAMPLES / n)


# ---------------------------------------------------------------------------- bundled plates

PLATES = {"dust_burst_v1": ("dust", 101), "energy_wisps_v1": ("wisps", 202), "particles_v1": ("particles", 303)}


def plates():
    root = presets_path().parent
    files = {}
    for preset in load_presets().values():
        for entry in preset["bundled_plates"]:
            style, seed = PLATES[entry["id"]]
            if entry["frames"] != BUNDLED_FRAMES:
                raise ValueError(f"{entry['id']}: presets say {entry['frames']} frames, plates have {BUNDLED_FRAMES}")
            for key, aspect in (("file", "16:9"), ("file_portrait", "9:16")):
                w, h = BUNDLED_SIZES[aspect]
                frames = list(procedural_plate(style, seed, w, h, BUNDLED_FRAMES))
                if entry["loopable"] and not loop_seam_ok(frames[0], frames[-1]):
                    raise ValueError(f"{entry['id']}: loop seam QA failed")
                path = root / entry[key]
                path.parent.mkdir(parents=True, exist_ok=True)
                encode(frames, path, w, h)
                files[entry[key]] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                     "bytes": path.stat().st_size, "size": [w, h], "frames": BUNDLED_FRAMES,
                                     "plate_id": entry["id"], "style": style, "seed": seed}
    (root / "plates" / "manifest.json").write_text(json.dumps(
        {"license": "MIT (procedurally generated by fixtures/pocket-vfx/make_fixtures.py --plates)",
         "fps": 24, "files": files}, indent=1) + "\n")


# ---------------------------------------------------------------------------- bundled SFX


def _env(t, peak, rise, fall):
    """Smooth one-peak envelope: raised-cosine rise over ``rise`` s, exponential decay with time constant ``fall``."""
    up = 0.5 - 0.5 * np.cos(np.pi * np.clip(t / rise, 0, 1)) if rise else np.ones_like(t)
    return np.where(t < peak, up, np.exp(-(t - peak) / fall))


def sfx_impact(rng):
    """Low thump (pitch drop 90 -> 45 Hz) + a short noise burst; energy peak ~ 80 ms."""
    t = np.arange(int(0.9 * RATE)) / RATE
    thump = np.sin(2 * np.pi * (45 * t + 45 * 0.06 * (1 - np.exp(-t / 0.06)))) * _env(t, 0.08, 0.08, 0.16)
    burst = rng.standard_normal(len(t)) * np.exp(-0.5 * ((t - 0.08) / 0.012) ** 2)
    return 0.8 * thump + 0.45 * burst


def sfx_aura(rng):
    """Rising shimmer: three detuned partials sweeping up, tremolo, energy peak ~ 0.9 s, then a decay."""
    t = np.arange(int(1.6 * RATE)) / RATE
    sig = sum(a * np.sin(2 * np.pi * (f0 * t + 0.5 * k * t * t) + ph)
              for f0, k, a, ph in ((330, 380, 1.0, 0.0), (497, 570, 0.7, 1.3), (743, 850, 0.5, 2.1)))
    sig = sig * (0.75 + 0.25 * np.sin(2 * np.pi * 9 * t)) * _env(t, 0.9, 0.9, 0.12)
    return 0.3 * sig + 0.03 * rng.standard_normal(len(t)) * _env(t, 0.9, 0.9, 0.12)


def sfx_blink(rng):
    """Sparkly sweep: a falling chirp plus random high pings; energy peak ~ 0.25 s."""
    t = np.arange(int(0.7 * RATE)) / RATE
    sweep = np.sin(2 * np.pi * (3200 * t - 3000 * t * t / (2 * 0.7))) * _env(t, 0.25, 0.25, 0.12)
    pings = np.zeros_like(t)
    for at, f in zip(rng.uniform(0.05, 0.5, 14), rng.uniform(2500, 7000, 14)):
        s = t - at
        pings += np.where(s > 0, np.sin(2 * np.pi * f * s) * np.exp(-s / 0.025), 0)
    return 0.55 * sweep + 0.12 * pings


SFX = {"impact_v1": (sfx_impact, 11), "aura_v1": (sfx_aura, 22), "blink_v1": (sfx_blink, 33)}


def write_wav(path, x):
    pcm = np.round(np.clip(x / max(np.abs(x).max(), 1e-9) * 0.9, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return pcm.astype(np.float64) / 32768


def sfx():
    root = presets_path().parent
    peaks = {}
    for stem, (make, seed) in SFX.items():
        path = root / "sfx" / f"{stem}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        peaks[f"sfx/{stem}.wav"] = energy_peak_ms(write_wav(path, make(np.random.default_rng(seed))))
    for target in {presets_path(), Path(presets_path()).parents[2] / "nodes" / "open_creative" / "open_creative"
                   / "pocket_vfx" / "presets.v1.json"}:
        doc = json.loads(target.read_text(encoding="utf-8"))
        for preset in doc["presets"]:
            preset["sfx"]["peak_offset_ms"] = peaks[preset["sfx"]["file"]]
        target.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(peaks)


if __name__ == "__main__":
    import sys

    if "--plates" in sys.argv[1:]:
        plates()
    elif "--sfx" in sys.argv[1:]:
        sfx()
    else:
        main()
