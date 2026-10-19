from alembic import context
from sqlalchemy import create_engine

import oc_api.models  # noqa: F401  (registers tables)
from oc_api.db import Base
from oc_api.settings import Settings

target_metadata = Base.metadata


def run() -> None:
    url = Settings().database_url
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata,
                          render_as_batch=conn.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


run()
