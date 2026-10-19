from datetime import timedelta

from sqlalchemy import select

from worker_helpers import add_job, load, make_ctx
from oc_api.db import utcnow
from oc_api.models import Asset, Job, JobCost, JobEvent, JobStage
from oc_worker.comfy.mock import MockRunner
from oc_worker.comfy.runner import ProviderRejected, ProviderUnavailable, SubmissionUnknown
from oc_worker.lease import LEASE_SECONDS, claim_next
from oc_worker.main import run_once


class Crash(BaseException):
    """Simulates the worker process dying."""


class CountingRunner(MockRunner):
    submits = 0

    def submit(self, workflow, bindings, submission_key):
        CountingRunner.submits += 1
        return super().submit(workflow, bindings, submission_key)


class CrashAfterSubmit(CountingRunner):
    def inspect(self, provider_job_id):
        raise Crash()


class FailingSubmit(MockRunner):
    def __init__(self, root, errors):
        super().__init__(root)
        self.errors = list(errors)

    def submit(self, workflow, bindings, submission_key):
        if self.errors:
            raise self.errors.pop(0)
        return super().submit(workflow, bindings, submission_key)


def test_claim_takes_lease_and_skips_leased_jobs(db_factory, project):
    jid = add_job(db_factory, project)
    now = utcnow()
    with db_factory() as db:
        job = claim_next(db, "w1", now)
        assert job.id == jid and job.lease_owner == "w1"
        assert job.lease_expires_at == now + timedelta(seconds=LEASE_SECONDS)
    with db_factory() as db:
        assert claim_next(db, "w2", now + timedelta(seconds=10)) is None
    with db_factory() as db:
        assert claim_next(db, "w2", now + timedelta(seconds=LEASE_SECONDS + 1)).lease_owner == "w2"


def test_terminal_jobs_are_never_claimed(db_factory, project):
    jid = add_job(db_factory, project)
    with db_factory() as db:
        db.get(Job, jid).state = "submission_unknown"
        db.commit()
    with db_factory() as db:
        assert claim_next(db, "w1", utcnow() + timedelta(hours=1)) is None


def test_background_job_succeeds_with_asset_and_events(ctx, db_factory, project):
    jid = add_job(db_factory, project)
    assert run_once(ctx)
    job = load(db_factory, Job, jid)
    assert job.state == "succeeded", job.error_message
    assert job.lease_owner is None and job.finished_at is not None
    (aid,) = job.output_asset_ids
    asset = load(db_factory, Asset, aid)
    assert asset.role == "background" and asset.state == "ready" and (asset.width, asset.height) == (720, 1280)
    with db_factory() as db:
        states = [e.payload_json.get("state") for e in db.scalars(
            select(JobEvent).where(JobEvent.job_id == jid).order_by(JobEvent.sequence))]
        cost = db.scalar(select(JobCost).where(JobCost.job_id == jid))
    assert states[-1] == "succeeded" and "running" in states
    assert cost.actual_units_nullable == 0  # mock profile spends nothing, known


def test_crash_after_submit_resumes_without_resubmitting(settings, db_factory, storage, mock_root, project):
    CountingRunner.submits = 0
    jid = add_job(db_factory, project)
    crashing = make_ctx(settings, db_factory, storage, CrashAfterSubmit(mock_root), worker_id="w1")
    try:
        run_once(crashing)
        raise AssertionError("expected crash")
    except Crash:
        pass
    with db_factory() as db:
        stage = db.scalar(select(JobStage).where(JobStage.job_id == jid))
        assert stage.provider_job_id and stage.submission_key
        db.get(Job, jid).lease_expires_at = utcnow() - timedelta(seconds=1)  # lease lapses
        db.commit()
    resumed = make_ctx(settings, db_factory, storage, CountingRunner(mock_root), worker_id="w2")
    assert run_once(resumed)
    assert load(db_factory, Job, jid).state == "succeeded"
    assert CountingRunner.submits == 1


def test_ambiguous_submit_is_quarantined(settings, db_factory, storage, mock_root, project):
    jid = add_job(db_factory, project)
    c = make_ctx(settings, db_factory, storage, FailingSubmit(mock_root, [SubmissionUnknown("read timeout")]))
    run_once(c)
    job = load(db_factory, Job, jid)
    assert job.state == "submission_unknown" and job.error_code == "submission_unknown"
    with db_factory() as db:
        cost = db.scalar(select(JobCost).where(JobCost.job_id == jid))
    assert cost.actual_units_nullable is None  # unknown, not zero


def test_interrupted_submit_retries_with_same_key(settings, db_factory, storage, mock_root, project):
    """Crash while 'submitting': the same key is resent; reject-on-duplicate makes this safe."""
    jid = add_job(db_factory, project)
    with db_factory() as db:
        db.add(JobStage(job_id=jid, stage_name="background_image", state="submitting", attempt=1,
                        submission_key=f"{jid}:background_image:1"))
        db.get(Job, jid).state = "submitting"
        db.commit()
    runner = MockRunner(mock_root)
    run_once(make_ctx(settings, db_factory, storage, runner))
    assert load(db_factory, Job, jid).state == "succeeded"
    assert runner.submit_count == 1


def test_interrupted_submit_already_delivered_is_unknown(settings, db_factory, storage, mock_root, project):
    jid = add_job(db_factory, project)
    key = f"{jid}:background_image:1"
    MockRunner(mock_root).submit("product_background_image_v1",
                                 {"prompt": "p", "seed": 1, "width": 64, "height": 64}, key)
    with db_factory() as db:
        db.add(JobStage(job_id=jid, stage_name="background_image", state="submitting", attempt=1,
                        submission_key=key))
        db.get(Job, jid).state = "submitting"
        db.commit()
    run_once(make_ctx(settings, db_factory, storage, MockRunner(mock_root)))
    assert load(db_factory, Job, jid).state == "submission_unknown"


def test_unreachable_provider_is_retried_then_succeeds(settings, db_factory, storage, mock_root, project):
    jid = add_job(db_factory, project)
    sleeps = []
    runner = FailingSubmit(mock_root, [ProviderUnavailable("refused"), ProviderUnavailable("refused")])
    run_once(make_ctx(settings, db_factory, storage, runner, sleep=sleeps.append))
    assert load(db_factory, Job, jid).state == "succeeded"
    assert len(sleeps) == 2 and all(0 < s <= 30 for s in sleeps)


def test_rejected_submission_fails_job(settings, db_factory, storage, mock_root, project):
    jid = add_job(db_factory, project)
    run_once(make_ctx(settings, db_factory, storage, FailingSubmit(mock_root, [ProviderRejected("bad graph")])))
    job = load(db_factory, Job, jid)
    assert job.state == "failed" and job.error_code == "provider_rejected"


def test_cancel_requested_before_start_submits_nothing(settings, db_factory, storage, mock_root, project):
    jid = add_job(db_factory, project)
    with db_factory() as db:
        job = db.get(Job, jid)
        job.state, job.cancellation_requested_at = "cancel_requested", utcnow()
        db.commit()
    runner = MockRunner(mock_root)
    run_once(make_ctx(settings, db_factory, storage, runner))
    assert load(db_factory, Job, jid).state == "cancelled"
    assert runner.submit_count == 0


def test_idle_worker_returns_false(ctx):
    assert run_once(ctx) is False
