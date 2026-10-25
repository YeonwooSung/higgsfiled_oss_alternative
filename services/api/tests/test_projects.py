import uuid

from conftest import new_client
from fastapi.testclient import TestClient

from oc_api.models import Asset


def test_session_cookie_is_httponly(client):
    r = client.post("/api/v1/sessions")
    assert r.status_code == 201
    cookie = r.headers["set-cookie"]
    assert "oc_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_routes_require_session(client):
    r = client.post("/api/v1/projects", json={"app_slug": "product-lock", "title": "x"})
    assert r.status_code == 401
    body = r.json()["error"]
    assert body["code"] == "session_required" and body["retryable"] is False and body["request_id"]


def test_create_and_get_project_with_default_config(session_client, project):
    assert project["current_revision"] == 1
    assert project["config"]["scene"]["preset_id"] == "soft_studio_v1"
    assert project["config"]["model_profile"] == "mock_v1"
    r = session_client.get(f"/api/v1/projects/{project['id']}")
    assert r.status_code == 200 and r.json()["title"] == "Can ad"


def test_unknown_app_slug_rejected(session_client):
    r = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch-x", "title": "x"})
    assert r.status_code == 422


def test_other_session_cannot_see_project(app, project):
    other = TestClient(app)
    other.post("/api/v1/sessions")
    assert other.get(f"/api/v1/projects/{project['id']}").status_code == 404


def test_patch_creates_new_immutable_revision(session_client, project):
    cfg = project["config"]
    cfg["transform"]["width_ratio"] = 0.5
    r = session_client.patch(f"/api/v1/projects/{project['id']}", json={"expected_revision": 1, "config": cfg})
    assert r.status_code == 200, r.text
    assert r.json()["current_revision"] == 2
    old = session_client.get(f"/api/v1/projects/{project['id']}/revisions/1").json()
    assert old["config"]["transform"]["width_ratio"] == 0.36


def test_stale_revision_conflicts(session_client, project):
    cfg = project["config"]
    session_client.patch(f"/api/v1/projects/{project['id']}", json={"expected_revision": 1, "config": cfg})
    r = session_client.patch(f"/api/v1/projects/{project['id']}", json={"expected_revision": 1, "config": cfg})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "revision_conflict"


def test_invalid_config_rejected_with_field(session_client, project):
    cfg = project["config"]
    cfg["transform"]["rotation_deg"] = 15
    r = session_client.patch(f"/api/v1/projects/{project['id']}", json={"expected_revision": 1, "config": cfg})
    assert r.status_code == 422
    assert "rotation_deg" in r.json()["error"]["field"]


def test_config_cannot_reference_asset_of_another_project(app, session_client, project):
    other = TestClient(app)
    other.post("/api/v1/sessions")
    foreign = other.post("/api/v1/projects", json={"app_slug": "product-lock", "title": "theirs"}).json()
    with app.state.db() as db:
        a = Asset(id=uuid.uuid4(), project_id=uuid.UUID(foreign["id"]), role="product", state="ready",
                  object_key="x", mime="image/png", bytes=1)
        db.add(a)
        db.commit()
        foreign_asset = str(a.id)
    cfg = project["config"]
    cfg["product_asset_id"] = foreign_asset
    r = session_client.patch(f"/api/v1/projects/{project['id']}", json={"expected_revision": 1, "config": cfg})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "asset_not_found"


def test_list_projects_is_session_scoped_and_newest_first(app, session_client):
    a = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "A"}).json()
    b = session_client.post("/api/v1/projects", json={"app_slug": "pocket-vfx", "title": "B"}).json()
    other = new_client(app); other.post("/api/v1/sessions")
    other.post("/api/v1/projects", json={"app_slug": "product-lock", "title": "X"})
    r = session_client.get("/api/v1/projects")
    assert r.status_code == 200
    rows = r.json()["projects"]
    assert [p["id"] for p in rows] == [b["id"], a["id"]]
    assert rows[0]["app_slug"] == "pocket-vfx" and set(rows[0]) == {"id", "app_slug", "title", "created_at", "updated_at"}


def test_list_projects_orders_by_last_revision_and_hides_deleted(app, session_client):
    a = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "A"}).json()
    b = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "B"}).json()
    session_client.patch(f"/api/v1/projects/{a['id']}", json={"expected_revision": 1, "config": a["config"]})
    assert [p["id"] for p in session_client.get("/api/v1/projects").json()["projects"]] == [a["id"], b["id"]]
    session_client.delete(f"/api/v1/projects/{b['id']}")
    assert [p["id"] for p in session_client.get("/api/v1/projects").json()["projects"]] == [a["id"]]
