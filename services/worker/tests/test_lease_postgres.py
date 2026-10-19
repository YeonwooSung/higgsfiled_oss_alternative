"""Concurrent claims on PostgreSQL: SKIP LOCKED must hand two workers different jobs."""

import os

import pytest

from oc_api.db import utcnow
from oc_worker.lease import claim_next
from worker_helpers import add_job

pytestmark = pytest.mark.skipif(not os.environ.get("OC_TEST_DATABASE_URL", "").startswith("postgresql"),
                                reason="needs PostgreSQL (set OC_TEST_DATABASE_URL)")


def test_concurrent_claims_skip_locked_rows(db_factory, project):
    first, second = add_job(db_factory, project), add_job(db_factory, project)
    a, b = db_factory(), db_factory()
    try:
        from sqlalchemy import select

        from oc_api.models import Job

        # Worker A holds the row lock on the oldest job inside an open transaction.
        locked = a.scalar(select(Job).where(Job.id == first).with_for_update())
        assert locked is not None
        claimed = claim_next(b, "w2", utcnow())
        assert claimed is not None and claimed.id == second  # skipped, not blocked
    finally:
        a.rollback()
        a.close()
        b.close()
