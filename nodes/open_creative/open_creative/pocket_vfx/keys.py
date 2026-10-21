"""Stage reuse keys of Pocket VFX tracking (spec §11). Pure; the API and the worker must compute the same key.

``cfg`` is the project config as stored in the revision (``PocketVfxConfig.model_dump(mode="json")``).
"""

from __future__ import annotations

import hashlib
import json

# Algorithm revision of the track + masks stages; bump when either changes its output for the same inputs.
# The ``smooth_s<sigma>_b<max shift>`` part names ``track.SMOOTH_SIGMA_FRAMES`` / ``SMOOTH_MAX_SHIFT_PX``
# (test_pv_keys pins them together). ``keys_v2``: the masks key no longer includes the anchor (R26).
TRACK_MODEL_REV = "lk_fb_median_v1+smooth_s1_b0.5+sam2.1_hiera_small+keys_v2"

_ANCHOR_FIELDS = ("frame", "x", "y", "follow", "track_window_frames")


def model_rev(model_profile: str) -> str:
    """The ``model_rev`` of a job: the algorithm revision combined with the job's model profile."""
    return f"{TRACK_MODEL_REV}@{model_profile}"


def _masks_fields(cfg: dict, source_sha: str, model_rev: str) -> dict:
    """What the masks prompts (``masks.build_prompts``) and their frames depend on."""
    return {
        "source_sha256": source_sha,
        "model_rev": model_rev,
        "trim": {"source_start_ms": cfg["trim"]["source_start_ms"], "duration_ms": cfg["trim"]["duration_ms"]},
        "aspect": cfg["output"]["aspect"],
        "subject_prompts": cfg.get("subject_prompts") or [],
        "mask_edits": cfg.get("mask_edits") or [],
    }


def _digest(kind: str, fields: dict) -> str:
    canon = json.dumps({"key": kind, **fields}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def track_key(cfg: dict, source_sha: str, model_rev: str) -> str:
    """Hash of everything the track output depends on. Effect, plate, audio and render settings are excluded."""
    anchor = cfg["anchor"]
    return _digest("track", {**_masks_fields(cfg, source_sha, model_rev),
                             "anchor": {k: anchor.get(k) for k in _ANCHOR_FIELDS},
                             "corrections": cfg.get("corrections") or []})


def masks_key(cfg: dict, source_sha: str, model_rev: str) -> str:
    """``track_key`` without the anchor and the point corrections (the masks prompts use neither): anchor- or
    correction-only edits re-run the track but reuse the masks stage output."""
    return _digest("masks", _masks_fields(cfg, source_sha, model_rev))
