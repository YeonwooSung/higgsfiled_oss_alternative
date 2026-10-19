from pathlib import Path

from open_creative.product_lock import prompts

REPO = Path(__file__).resolve().parents[3]


def test_packaged_presets_match_repo_presets():
    packaged = Path(prompts.__file__).with_name("presets.v1.json")
    assert packaged.read_text() == (REPO / "presets" / "product-lock" / "presets.v1.json").read_text()


def test_presets_resolve_without_repo_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(prompts, "DEFAULT_PRESETS", tmp_path / "missing.json")
    monkeypatch.delenv("OC_PRODUCT_LOCK_PRESETS", raising=False)
    assert prompts.presets_path() == Path(prompts.__file__).with_name("presets.v1.json")
