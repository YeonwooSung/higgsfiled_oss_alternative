import pytest
from fastapi.testclient import TestClient

from oc_api.app import create_app
from oc_api.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "storage",
        signing_secret="test-secret",
        cookie_secure=False,
        default_profile="mock_v1",
        public_base_url="http://testserver",
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def new_client(app):
    return TestClient(app)


@pytest.fixture
def session_client(client):
    r = client.post("/api/v1/sessions")
    assert r.status_code == 201
    return client


@pytest.fixture
def project(session_client):
    r = session_client.post("/api/v1/projects", json={"app_slug": "product-lock", "title": "Can ad"})
    assert r.status_code == 201, r.text
    return r.json()
