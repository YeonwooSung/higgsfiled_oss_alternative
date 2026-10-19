from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://oc:oc@127.0.0.1:5432/oc"
    storage_backend: str = "local"  # local | s3
    storage_dir: Path = REPO_ROOT / ".data" / "storage"
    s3_bucket: str = "open-creative"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    signing_secret: str = Field(default="dev-only-change-me", min_length=8)
    public_base_url: str = "http://localhost:3000"
    cookie_secure: bool = True
    session_ttl_days: int = 7
    default_profile: str = "open_weights_v1"
    workflows_dir: Path = REPO_ROOT / "workflows" / "product-lock"
    comfy_base_url: str | None = Field(default=None, validation_alias="COMFY_BASE_URL")
    upload_url_ttl_s: int = 600
    read_url_ttl_s: int = 300
    idempotency_retention_h: int = 24
    max_gpu_jobs_per_session: int = 1
    max_active_renders_per_project: int = 1
    max_queued_per_session: int = 3
    work_dir: Path = REPO_ROOT / ".data" / "work"
