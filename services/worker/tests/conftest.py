from datetime import timedelta

import pytest

from oc_api.db import Base, make_sessionmaker, utcnow
from oc_api.models import Project, ProjectRevision
from oc_api.models import Session as SessionRow
from oc_api.product_lock.config import default_config
from oc_api.settings import Settings
from oc_api.storage import LocalStorage
from oc_worker.comfy.mock import MockRunner
from worker_helpers import make_ctx


@pytest.fixture
def settings(tmp_path):
    from conftest_db import database_url_for_tests

    return Settings(database_url=database_url_for_tests(tmp_path), storage_dir=tmp_path / "storage",
                    signing_secret="test-secret", default_profile="mock_v1", work_dir=tmp_path / "work")


@pytest.fixture
def db_factory(settings):
    factory = make_sessionmaker(settings.database_url)
    Base.metadata.create_all(factory.kw["bind"])
    return factory


@pytest.fixture
def storage(settings):
    return LocalStorage(settings.storage_dir, settings.signing_secret)


@pytest.fixture
def mock_root(tmp_path):
    return tmp_path / "mock-provider"


@pytest.fixture
def ctx(settings, db_factory, storage, mock_root):
    return make_ctx(settings, db_factory, storage, MockRunner(mock_root))


@pytest.fixture
def project(db_factory):
    with db_factory() as db:
        s = SessionRow(expires_at=utcnow() + timedelta(days=1))
        db.add(s)
        db.flush()
        p = Project(session_id=s.id, app_slug="product-lock", title="t", current_revision=1)
        db.add(p)
        db.flush()
        db.add(ProjectRevision(project_id=p.id, revision=1, config_json=default_config("mock_v1").model_dump(mode="json"),
                               config_schema_version=1))
        db.commit()
        return p
