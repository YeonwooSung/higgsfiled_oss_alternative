"""Generate the synthetic Camera Sketch scenes (MIT, procedurally rendered, no third-party imagery).

Run: uv run python fixtures/camera-sketch/make_fixtures.py
Writes per scene: <name>.png (RGB), <name>.depth.png (16-bit GT inverse depth, near = bright)
and scenes.json. Scenes are textured (noise/stripes/grids) so feature tracking has corners to
follow. Fixed seeds -> byte-identical outputs. The PNGs are also copied to
apps/camera-sketch/public/samples/. (parity.v1.json is added by a later task.)
"""

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from open_creative.camera_sketch.scene import DAV2_SMALL, encode_depth_png16

OUT = Path(__file__).parent
SAMPLES = OUT.parents[1] / "apps" / "camera-sketch" / "public" / "samples"


def noise(rng, h, w, amp):
    return rng.normal(0.0, amp, (h, w, 1)).astype(np.float32)


def grid(h, w):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    return y, x


def shade(color, lum):
    return np.clip(np.asarray(color, np.float32)[None, None, :] * lum[..., None], 0, 255)


def room():
    h, w = 540, 960
    rng = np.random.default_rng(101)
    y, x = grid(h, w)
    horizon = 250.0
    d = np.full((h, w), 0.20, np.float32)  # back wall
    floor = y > horizon
    d[floor] = 0.20 + 0.75 * (y[floor] - horizon) / (h - horizon)
    lum = np.ones((h, w), np.float32)
    # wall: vertical wallpaper stripes; floor: wood planks with perspective-ish spacing
    wall_rgb = shade((190, 176, 150), 0.85 + 0.15 * (np.floor(x / 24) % 2))
    plank = (np.floor((x - w / 2) / (40 + 0.35 * (y - horizon))) % 2)
    floor_rgb = shade((140, 98, 62), 0.80 + 0.2 * plank + 0.0 * y)
    img = np.where(floor[..., None], floor_rgb, wall_rgb)
    # window on the wall
    win = (x > 120) & (x < 300) & (y > 60) & (y < 200)
    img[win] = (200, 225, 245)
    img[win & (((x - 120) % 60 < 4) | ((y - 60) % 70 < 4))] = (240, 240, 240)
    # chair-like box: seat block + backrest
    seat = (x > 470) & (x < 760) & (y > 330) & (y < 470)
    back = (x > 470) & (x < 760) & (y > 190) & (y < 335)
    d[seat] = 0.95
    d[back] = 0.62
    seat_rgb = shade((60, 90, 150), 0.82 + 0.18 * (np.floor(y / 10) % 2))
    back_rgb = shade((48, 72, 120), 0.8 + 0.2 * (np.floor(x / 14) % 2))
    img = np.where(seat[..., None], seat_rgb, img)
    img = np.where(back[..., None], back_rgb, img)
    img = img + noise(rng, h, w, 6.0)
    return img, d, (0.62, 0.55)


def product():
    h, w = 540, 960
    rng = np.random.default_rng(202)
    y, x = grid(h, w)
    horizon = 300.0
    d = np.full((h, w), 0.15, np.float32)
    table = y > horizon
    d[table] = 0.15 + 0.6 * (y[table] - horizon) / (h - horizon)
    wall_rgb = shade((90, 110, 130), 0.8 + 0.2 * (np.sin(x / 9.0 + 0.002 * y * y) > 0))
    table_rgb = shade((176, 140, 96), 0.85 + 0.15 * (np.floor((y - horizon) / 18) % 2))
    img = np.where(table[..., None], table_rgb, wall_rgb)
    # bottle: cylinder, inverse depth bulges toward the centre line
    cx, half = 480.0, 80.0
    body = (np.abs(x - cx) < half) & (y > 190) & (y < 440)
    neck = (np.abs(x - cx) < 28) & (y > 110) & (y <= 190)
    prof = np.sqrt(np.clip(1 - ((x - cx) / half) ** 2, 0, 1))
    d[body] = (0.62 + 0.18 * prof)[body]
    d[neck] = 0.74
    lab = body & (y > 260) & (y < 360)
    bottle = shade((30, 120, 80), 0.55 + 0.45 * prof)
    bottle[lab] = shade((235, 230, 210), 0.6 + 0.4 * prof)[lab]
    bottle[lab & (((y - 260) % 20 < 3))] = (60, 60, 60)
    img = np.where((body | neck)[..., None], bottle, img)
    img = img + noise(rng, h, w, 6.0)
    return img, d, (0.5, 0.58)


def facade():
    h, w = 1024, 576
    rng = np.random.default_rng(303)
    y, x = grid(h, w)
    sky = y < 170
    d = np.zeros((h, w), np.float32)  # sky = infinity -> inverse depth 0
    facade_m = ~sky
    d[facade_m] = 0.30 + 0.30 * (y[facade_m] - 170) / (h - 170) + 0.15 * x[facade_m] / w
    wall_rgb = shade((176, 150, 128), 0.88 + 0.12 * (np.floor(y / 6) % 2))
    img = np.where(sky[..., None], shade((150, 190, 235), 1.0 - 0.3 * y / 170), wall_rgb)
    # window grid recessed slightly; balconies protruding
    cell_w, cell_h = 96, 120
    gx, gy = (x - 24) % cell_w, (y - 200) % cell_h
    win = facade_m & (y > 200) & (gx < 60) & (gy < 80)
    d[win] -= 0.05
    img = np.where(win[..., None], shade((40, 60, 90), 0.7 + 0.3 * (gx[..., None][..., 0] / 60)), img)
    bal = facade_m & (y > 200) & (gx < 72) & (gy > 84) & (gy < 96)
    d[bal] += 0.12
    img = np.where(bal[..., None], shade((90, 90, 96), 1.0 + 0 * gx), img)
    img = img + noise(rng, h, w, 6.0)
    return img, d, (0.5, 0.6)


SCENES = {"room": room, "product": product, "facade": facade}


def main():
    SAMPLES.mkdir(parents=True, exist_ok=True)
    meta = {"depth_convention": {"kind": DAV2_SMALL.kind, "note": "GT inverse depth: larger = nearer; sky = 0",
                                  "png16": "linear min..max -> 0..65535"}, "scenes": {}}
    for name, fn in SCENES.items():
        img, d, (tu, tv) = fn()
        rgb = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
        png = OUT / f"{name}.png"
        rgb.save(png, optimize=True)
        data, dm = encode_depth_png16(d)
        (OUT / f"{name}.depth.png").write_bytes(data)
        meta["scenes"][name] = {
            "image": png.name,
            "depth": f"{name}.depth.png",
            "width": rgb.width,
            "height": rgb.height,
            "sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
            "depth_png16": {"min": dm["min"], "max": dm["max"]},
            "target_uv01": [tu, tv],
        }
        shutil.copyfile(png, SAMPLES / png.name)
    (OUT / "scenes.json").write_text(json.dumps(meta, indent=2) + "\n")


if __name__ == "__main__":
    main()
