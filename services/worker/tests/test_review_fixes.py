import io
import uuid
from datetime import timedelta

import httpx
import pytest
from PIL import Image

from oc_api.db import utcnow
from oc_api.models import Asset, Job
from oc_worker.comfy.mock import MockRunner
from oc_worker.comfy.runner import ProviderUnavailable
from oc_worker.lease import MAX_ATTEMPTS, claim_next
from oc_worker.main import run_once
from open_creative.media.image import MediaError, load_mask_png
from test_executors import inspect, upload_raw
from worker_helpers import add_job, load, make_ctx


def huge_png() -> bytes:
    buf = io.BytesIO()
    Image.new("L", (5000, 5000)).save(buf, "PNG")  # 25 MP, tiny file
    return buf.getvalue()


def test_load_mask_png_blocks_pixel_bombs():
    with pytest.raises(MediaError) as e:
        load_mask_png(huge_png())
    assert e.value.code == "too_many_pixels"


def test_huge_mask_upload_is_rejected(ctx, db_factory, storage, project):
    aid = upload_raw(db_factory, storage, project, huge_png(), role="mask")
    job = inspect(ctx, db_factory, project, aid)
    assert job.state == "failed" and job.error_code == "too_many_pixels"
    assert load(db_factory, Asset, aid).state == "rejected"


def test_job_that_keeps_killing_workers_is_failed(db_factory, project):
    jid = add_job(db_factory, project)
    with db_factory() as db:
        job = db.get(Job, jid)
        job.state, job.attempts, job.lease_owner = "running", MAX_ATTEMPTS, "dead"
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    with db_factory() as db:
        assert claim_next(db, "w2", utcnow()) is None
    job = load(db_factory, Job, jid)
    assert job.state == "failed" and job.error_code == "worker_crashed" and job.lease_owner is None


class StealLeaseOnCollect(MockRunner):
    def __init__(self, root, db_factory, job_id):
        super().__init__(root)
        self.db_factory, self.job_id = db_factory, job_id

    def collect(self, provider_job_id, target_dir, workflow=None):
        with self.db_factory() as db:
            db.get(Job, self.job_id).lease_owner = "w2"  # another worker took over after a stall
            db.commit()
        return super().collect(provider_job_id, target_dir, workflow)


def test_worker_that_lost_its_lease_does_not_finish(settings, db_factory, storage, mock_root, project):
    jid = add_job(db_factory, project)
    run_once(make_ctx(settings, db_factory, storage, StealLeaseOnCollect(mock_root, db_factory, jid)))
    job = load(db_factory, Job, jid)
    assert job.state != "succeeded" and job.lease_owner == "w2"


def test_sdk_pre_submit_network_error_is_retryable(tmp_path):
    import json

    from oc_worker.comfy.sdk import ComfySdkRunner
    (tmp_path / "g.api.json").write_text(json.dumps({"1": {"inputs": {"image": "", "text": ""}}, "2": {"inputs": {}}}))
    (tmp_path / "product_background_video_v1.bindings.json").write_text(json.dumps({
        "workflow": "product_background_video_v1", "graph": "g.api.json",
        "bindings": {"image": {"node_id": "1", "input": "image", "type": "image"},
                     "prompt": {"node_id": "1", "input": "text", "type": "string"}},
        "outputs": {"video": {"node_id": "2"}}}))

    def boom(path):
        raise httpx.ConnectError("refused")

    client = type("C", (), {})()
    client.workflows = type("W", (), {"from_json": staticmethod(lambda g: type("WF", (), {"set_input": lambda *a: None})())})()
    client.assets = type("A", (), {"from_file": staticmethod(boom)})()
    with pytest.raises(ProviderUnavailable):
        ComfySdkRunner(client, tmp_path).submit("product_background_video_v1", {"image": "/x.png", "prompt": "p"}, "k")
