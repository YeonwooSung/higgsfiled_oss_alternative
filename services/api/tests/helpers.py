"""Test helpers that place ready assets directly (bypassing the inspection worker)."""

import uuid

import numpy as np

from oc_api.models import Asset, Job, Project
from open_creative.media.image import mask_to_png_bytes, to_png_bytes


def put_asset(app, project_id, role, data, mime, width, height, state="ready", meta=None):
    with app.state.db() as db:
        project = db.get(Project, uuid.UUID(project_id))
        aid = uuid.uuid4()
        key = f"{project.session_id}/{project.id}/{aid}/{role}.png"
        app.state.storage.put_bytes(key, data, mime)
        db.add(Asset(id=aid, project_id=project.id, role=role, state=state, object_key=key, mime=mime,
                     bytes=len(data), width=width, height=height, meta_json=meta))
        db.commit()
        return str(aid)


def ready_product(app, project_id, w=80, h=120, mask_shape=None, empty_mask=False):
    rgb = np.full((h, w, 3), 200, np.uint8)
    product = put_asset(app, project_id, "product", to_png_bytes(rgb), "image/png", w, h)
    mh, mw = mask_shape or (h, w)
    m = np.zeros((mh, mw), np.float32)
    if not empty_mask:
        m[10:mh - 10, 10:mw - 10] = 1.0
    mask = put_asset(app, project_id, "mask", mask_to_png_bytes(m), "image/png", mw, mh)
    return product, mask


def configure(client, project, **changes):
    cur = client.get(f"/api/v1/projects/{project['id']}").json()
    cfg = cur["config"]
    for path, value in changes.items():
        target = cfg
        parts = path.split("__")
        for p in parts[:-1]:
            target = target[p]
        target[parts[-1]] = value
    r = client.patch(f"/api/v1/projects/{project['id']}", json={"expected_revision": cur["current_revision"],
                                                               "config": cfg})
    assert r.status_code == 200, r.text
    return r.json()


def set_job_state(app, job_id, state):
    with app.state.db() as db:
        db.get(Job, uuid.UUID(job_id)).state = state
        db.commit()
