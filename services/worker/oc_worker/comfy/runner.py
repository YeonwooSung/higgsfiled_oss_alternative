"""Adapter contract between the job worker and an execution provider.

These method names are our interface (shared spec §4.1), not the SDK's.
Error classes encode what the worker may safely do next:

- ProviderRejected     deterministic refusal before execution → fail the stage, safe to fix & retry
- ProviderUnavailable  the request never reached the provider → safe to retry the same submission
- SubmissionUnknown    the request may have been accepted (timeout, dropped connection) →
                       never resubmit automatically; quarantine as `submission_unknown`
- SubmissionKeyReused  the provider already saw this key (reject-on-duplicate, no replay)
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Literal, Protocol, TypedDict

ProviderState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
TERMINAL_PROVIDER_STATES = {"succeeded", "failed", "cancelled"}


class ProviderError(Exception):
    pass


class ProviderRejected(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class SubmissionUnknown(ProviderError):
    pass


class SubmissionKeyReused(ProviderError):
    pass


class ProviderStatus(TypedDict):
    state: ProviderState
    progress: float | None
    error: str | None


class WorkflowRunner(Protocol):
    profile: str

    def capabilities(self) -> dict: ...

    def submit(self, workflow: str, bindings: dict, submission_key: str) -> str: ...

    def inspect(self, provider_job_id: str) -> ProviderStatus: ...

    def events(self, provider_job_id: str, cursor: str | None = None) -> Iterator[dict]: ...

    def collect(self, provider_job_id: str, target_dir: Path, workflow: str) -> list[Path]: ...

    def cancel(self, provider_job_id: str) -> ProviderStatus: ...
