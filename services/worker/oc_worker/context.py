from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from oc_api.settings import Settings
from oc_api.storage import Storage

from .comfy.runner import WorkflowRunner


@dataclass
class WorkerContext:
    settings: Settings
    db_factory: sessionmaker
    storage: Storage
    runner_for: Callable[[str], WorkflowRunner]
    worker_id: str
    sleep: Callable[[float], None] = field(default=time.sleep)

    @property
    def work_dir(self) -> Path:
        return Path(self.settings.work_dir)
