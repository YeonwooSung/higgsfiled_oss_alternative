"""ComfySdkRunner maps our adapter contract onto comfy-sdk 0.5.0 (no network)."""

import json

import httpx
import pytest
from comfy_sdk import IdempotencyKeyReuse, InvalidWorkflow

from oc_worker.comfy.runner import ProviderRejected, ProviderUnavailable, SubmissionKeyReused, SubmissionUnknown
from oc_worker.comfy.sdk import ComfySdkRunner

GRAPH = {"1": {"inputs": {"image": "", "text": ""}}, "2": {"inputs": {}}}
MANIFEST = {
    "workflow": "product_background_video_v1", "graph": "g.api.json",
    "bindings": {"image": {"node_id": "1", "input": "image", "type": "image"},
                 "prompt": {"node_id": "1", "input": "text", "type": "string"}},
    "outputs": {"video": {"node_id": "2"}},
}


class FakeWorkflow:
    def __init__(self, graph):
        self.json = graph
        self.inputs = []

    def set_input(self, node_id, field, value):
        self.inputs.append((node_id, field, value))


class FakeOutput:
    def __init__(self, name, data):
        self.name, self.data = name, data

    def to_file(self, path):
        open(path, "wb").write(self.data)
        return path


class FakeJob:
    def __init__(self, status="running", outputs=None):
        self.id, self.status, self.error = "job-1", status, None
        self._outputs = outputs or {}
        self.cancelled = False

    def refresh(self):
        return self

    def get_outputs(self, node_id):
        return self._outputs.get(node_id, [])

    def cancel(self):
        self.cancelled = True
        self.status = "canceled"
        return self


class FakeClient:
    def __init__(self, job=None, submit_error=None):
        self.job = job or FakeJob()
        self.submit_error = submit_error
        self.submitted = []
        self.workflows = type("W", (), {"from_json": staticmethod(lambda g: FakeWorkflow(g))})()
        self.assets = type("A", (), {"from_file": staticmethod(lambda p: ("asset", p))})()
        self.jobs = type("J", (), {"get": staticmethod(lambda _id: self.job)})()

    def submit(self, wf, *, idempotency_key=None):
        if self.submit_error:
            raise self.submit_error
        self.submitted.append((wf, idempotency_key))
        return self.job


@pytest.fixture
def wfdir(tmp_path):
    (tmp_path / "g.api.json").write_text(json.dumps(GRAPH))
    (tmp_path / "product_background_video_v1.bindings.json").write_text(json.dumps(MANIFEST))
    return tmp_path


def test_submit_binds_inputs_and_uses_submission_key(wfdir, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"png")
    client = FakeClient()
    runner = ComfySdkRunner(client, wfdir)
    jid = runner.submit("product_background_video_v1", {"image": str(img), "prompt": "slow"}, "job:stage:1")
    assert jid == "job-1"
    wf, key = client.submitted[0]
    assert key == "job:stage:1"
    assert ("1", "image", ("asset", str(img))) in wf.inputs
    assert ("1", "text", "slow") in wf.inputs


@pytest.mark.parametrize("error, expected", [
    (httpx.ReadTimeout("t"), SubmissionUnknown),
    (httpx.RemoteProtocolError("dropped"), SubmissionUnknown),
    (httpx.ConnectError("refused"), ProviderUnavailable),
    (IdempotencyKeyReuse("reused"), SubmissionKeyReused),
    (InvalidWorkflow("bad"), ProviderRejected),
])
def test_submit_error_mapping(wfdir, error, expected):
    runner = ComfySdkRunner(FakeClient(submit_error=error), wfdir)
    with pytest.raises(expected):
        runner.submit("product_background_video_v1", {"image": "/x.png", "prompt": "p"}, "k")


def test_invalid_values_never_reach_the_provider(wfdir):
    client = FakeClient()
    with pytest.raises(ProviderRejected):
        ComfySdkRunner(client, wfdir).submit("product_background_video_v1", {"prompt": "p"}, "k")
    assert client.submitted == []


def test_inspect_maps_provider_status(wfdir):
    runner = ComfySdkRunner(FakeClient(job=FakeJob(status="canceled")), wfdir)
    assert runner.inspect("job-1")["state"] == "cancelled"
    runner = ComfySdkRunner(FakeClient(job=FakeJob(status="expired")), wfdir)
    assert runner.inspect("job-1")["state"] == "failed"


def test_collect_downloads_manifest_outputs(wfdir, tmp_path):
    job = FakeJob(status="succeeded", outputs={"2": [FakeOutput("bg.mp4", b"vid")]})
    paths = ComfySdkRunner(FakeClient(job=job), wfdir).collect("job-1", tmp_path / "out", "product_background_video_v1")
    assert [p.name for p in paths] == ["video_0.mp4"]
    assert paths[0].read_bytes() == b"vid"


def test_cancel_reports_provider_state(wfdir):
    job = FakeJob()
    res = ComfySdkRunner(FakeClient(job=job), wfdir).cancel("job-1")
    assert job.cancelled and res["state"] == "cancelled"
