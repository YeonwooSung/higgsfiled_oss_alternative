from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from oc_api.db import Base
import oc_api.models  # noqa: F401

API_DIR = Path(__file__).resolve().parents[1]


def test_migrations_match_models(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("OC_DATABASE_URL", url)
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set(Base.metadata.tables)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
    command.downgrade(cfg, "base")
