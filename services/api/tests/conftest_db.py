import os

from sqlalchemy import create_engine

import oc_api.models  # noqa: F401
from oc_api.db import Base


def database_url_for_tests(tmp_path) -> str:
    """OC_TEST_DATABASE_URL runs the suite on PostgreSQL (tables are recreated per test)."""
    url = os.environ.get("OC_TEST_DATABASE_URL")
    if not url:
        return f"sqlite+pysqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()
    return url
