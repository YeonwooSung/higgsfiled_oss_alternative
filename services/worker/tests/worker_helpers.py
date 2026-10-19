import uuid

from oc_api.models import Job, JobCost, Project, ProjectRevision
from oc_worker.context import WorkerContext


def make_ctx(settings, db_factory, storage, runner, worker_id="w1", sleep=lambda s: None):
    return WorkerContext(settings=settings, db_factory=db_factory, storage=storage,
                         runner_for=lambda profile: runner, worker_id=worker_id, sleep=sleep)


def add_job(db_factory, project, kind="product.background", options=None, uses_gpu=True, reserved=120,
            revision=1, profile="mock_v1"):
    with db_factory() as db:
        job = Job(project_id=project.id, session_id=project.session_id, input_revision=revision, kind=kind,
                  options_json=options if options is not None else {"preset_id": "neon_studio_v1", "seed": 3},
                  model_profile=profile, uses_gpu=uses_gpu, state="queued")
        db.add(job)
        db.flush()
        db.add(JobCost(job_id=job.id, reserved_units=reserved, unit="gpu_seconds"))
        db.commit()
        return job.id


def set_revision_config(db_factory, project, config: dict, revision: int = 1):
    with db_factory() as db:
        rev = db.get(ProjectRevision, (project.id, revision))
        if rev is None:
            db.add(ProjectRevision(project_id=project.id, revision=revision, config_json=config,
                                   config_schema_version=1))
            db.get(Project, project.id).current_revision = revision
        else:
            rev.config_json = config
        db.commit()


def load(db_factory, model, id_):
    with db_factory() as db:
        return db.get(model, id_ if not isinstance(id_, str) else uuid.UUID(id_))
