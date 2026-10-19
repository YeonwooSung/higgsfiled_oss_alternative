"""Worker entrypoint: claim → execute → settle, forever."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import time
import uuid

from oc_api.cleanup import run_cleanup
from oc_api.db import make_sessionmaker, utcnow
from oc_api.settings import Settings
from oc_api.storage import make_storage
from open_creative.comfy.profiles import MOCK_PROFILE

from .comfy.mock import MockRunner
from .comfy.sdk import ComfySdkRunner
from .context import WorkerContext
from .executors import EXECUTORS
from .lease import claim_next
from .stages import Cancelled, JobRun, LeaseLost, StageFailed, SubmissionUnknownError

log = logging.getLogger("oc_worker")


def run_once(ctx: WorkerContext) -> bool:
    with ctx.db_factory() as db:
        job = claim_next(db, ctx.worker_id, utcnow())
        if job is None:
            return False
        job_id = job.id
    run = JobRun(ctx, job_id)
    try:
        _execute(run)
    finally:
        run.close()
    return True


def _execute(run: JobRun) -> None:
    try:
        _run_and_finish(run)
    except LeaseLost:
        run.db.rollback()
        log.warning("lease lost job=%s; another worker owns it", run.job.id)


def _run_and_finish(run: JobRun) -> None:
    job = run.job
    executor = EXECUTORS.get(job.kind)
    try:
        if executor is None:
            raise StageFailed("unsupported_kind", f"no executor for {job.kind}")
        run.check_cancel()
        if job.state in ("queued",):
            run.transition("preparing")
        outputs, result = executor(run)
        run.check_cancel()
        run.finish("succeeded", outputs=outputs, result=result)
    except Cancelled:
        run.db.rollback()
        run.finish("cancelled")
    except SubmissionUnknownError as e:
        run.db.commit()
        run.finish("submission_unknown", error_code="submission_unknown",
                   message="The provider may have accepted this job; it was not resubmitted automatically.")
        log.warning("submission unknown job=%s: %s", job.id, e)
    except StageFailed as e:
        run.db.commit()
        run.finish("failed", error_code=e.code, message=e.message)
    except LeaseLost:
        raise
    except Exception:  # noqa: BLE001
        log.exception("job %s crashed", job.id)
        run.db.rollback()
        run.finish("failed", error_code="internal_error", message="Processing failed unexpectedly.")


def build_context(settings: Settings, worker_id: str | None = None) -> WorkerContext:
    mock = MockRunner(settings.work_dir.parent / "mock-provider")
    sdk: list[ComfySdkRunner] = []

    def runner_for(profile: str):
        if profile == MOCK_PROFILE:
            return mock
        if not sdk:
            sdk.append(ComfySdkRunner.from_env(settings.workflows_dir))
        return sdk[0]

    return WorkerContext(settings=settings, db_factory=make_sessionmaker(settings.database_url),
                         storage=make_storage(settings), runner_for=runner_for,
                         worker_id=worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Open Creative Apps job worker")
    parser.add_argument("--concurrency", type=int, default=2, help="CPU job slots in this process")
    parser.add_argument("--idle-sleep", type=float, default=1.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    def loop(i: int) -> None:
        ctx = build_context(settings)
        ctx.worker_id = f"{ctx.worker_id}-{i}"
        last_cleanup = 0.0
        while not stop.is_set():
            try:
                if i == 0 and time.monotonic() - last_cleanup > 60:
                    last_cleanup = time.monotonic()
                    run_cleanup(ctx.db_factory, ctx.storage)
                if not run_once(ctx):
                    stop.wait(args.idle_sleep)
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                stop.wait(args.idle_sleep)

    threads = [threading.Thread(target=loop, args=(i,), daemon=True) for i in range(args.concurrency)]
    for t in threads:
        t.start()
    log.info("worker started with %d slots", args.concurrency)
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
