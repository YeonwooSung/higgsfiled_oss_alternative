"""Output frame sizes and product placement inside the output frame."""

from __future__ import annotations

from dataclasses import dataclass

OUTPUT_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280)}
DRAFT_SIZES = {"16:9": (640, 360), "9:16": (360, 640)}

WIDTH_RATIO_MIN = 0.15
WIDTH_RATIO_MAX = 0.70


def output_size(aspect: str, draft: bool = False) -> tuple[int, int]:
    table = DRAFT_SIZES if draft else OUTPUT_SIZES
    if aspect not in table:
        raise ValueError(f"unsupported aspect {aspect!r}")
    return table[aspect]


@dataclass(frozen=True)
class Placement:
    """Integer rectangle of the resampled product in output pixel space."""

    x0: int
    y0: int
    w: int
    h: int

    def visible(self, out_w: int, out_h: int) -> tuple[slice, slice, slice, slice]:
        """Return (frame_ys, frame_xs, product_ys, product_xs) clipped to the frame."""
        fx0, fy0 = max(self.x0, 0), max(self.y0, 0)
        fx1, fy1 = min(self.x0 + self.w, out_w), min(self.y0 + self.h, out_h)
        fx1, fy1 = max(fx1, fx0), max(fy1, fy0)
        return (
            slice(fy0, fy1),
            slice(fx0, fx1),
            slice(fy0 - self.y0, fy1 - self.y0),
            slice(fx0 - self.x0, fx1 - self.x0),
        )


def place_product(
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    center_x: float,
    center_y: float,
    width_ratio: float,
    rotation_deg: float = 0,
) -> Placement:
    if rotation_deg != 0:
        raise ValueError("rotation_deg must be 0 in P0")
    if not WIDTH_RATIO_MIN <= width_ratio <= WIDTH_RATIO_MAX:
        raise ValueError(f"width_ratio must be within [{WIDTH_RATIO_MIN}, {WIDTH_RATIO_MAX}]")
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source size must be positive")
    w = max(1, round(width_ratio * out_w))
    h = max(1, round(w * src_h / src_w))
    return Placement(
        x0=round(center_x * out_w - w / 2),
        y0=round(center_y * out_h - h / 2),
        w=w,
        h=h,
    )
