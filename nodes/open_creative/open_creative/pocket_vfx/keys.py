"""Stage reuse keys of Pocket VFX tracking (spec §11). Pure; the API and the worker must compute the same key.

``cfg`` is the project config as stored in the revision (``PocketVfxConfig.model_dump(mode="json")``).
"""

from __future__ import annotations

import hashlib
import json

# Algorithm revision of the track + masks stages; bump when either changes its output for the same inputs.
# The ``smooth_s<sigma>_b<max shift>`` part names ``track.SMOOTH_SIGMA_FRAMES`` / ``SMOOTH_MAX_SHIFT_PX``
# (test_pv_keys pins them together).
TRACK_MODEL_REV = "lk_fb_median_v1+smooth_s1_b0.5+sam2.1_hiera_small"

_ANCHOR_FIELDS = ("frame", "x", "y", "follow", "track_window_frames")


def model_rev(model_profile: str) -> str:
    """The ``model_rev`` of a job: the algorithm revision combined with the job's model profile."""
    return f"{TRACK_MODEL_REV}@{model_profile}"


def _fields(cfg: dict, source_sha: str, model_rev: str, *, corrections: bool) -> dict:
    anchor = cfg["anchor"]
    fields = {
        "source_sha256": source_sha,
        "model_rev": model_rev,
        "trim": {"source_start_ms": cfg["trim"]["source_start_ms"], "duration_ms": cfg["trim"]["duration_ms"]},
        "aspect": cfg["output"]["aspect"],
        "anchor": {k: anchor.get(k) for k in _ANCHOR_FIELDS},
        "subject_prompts": cfg.get("subject_prompts") or [],
        "mask_edits": cfg.get("mask_edits") or [],
    }
    if corrections:
        fields["corrections"] = cfg.get("corrections") or []
    return fields


def _digest(kind: str, fields: dict) -> str:
    canon = json.dumps({"key": kind, **fields}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def track_key(cfg: dict, source_sha: str, model_rev: str) -> str:
    """Hash of everything the track output depends on. Effect, plate, audio and render settings are excluded."""
    return _digest("track", _fields(cfg, source_sha, model_rev, corrections=True))


def masks_key(cfg: dict, source_sha: str, model_rev: str) -> str:
    """``track_key`` without the point corrections: correction-only edits reuse the masks stage output."""
    return _digest("masks", _fields(cfg, source_sha, model_rev, corrections=False))
