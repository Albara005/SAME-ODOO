"""Runtime configuration, read from environment variables (prefix NOVARA_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

_DEV_SECRET = "dev-only-insecure-secret-change-me"


@dataclass(frozen=True)
class Settings:
    env: str = "development"
    database_url: str = "sqlite:///./novara-dev.db"
    jwt_secret: str = _DEV_SECRET
    jwt_algorithm: str = "HS256"
    session_ttl_minutes: int = 60 * 12
    storage_root: str = "./storage"
    max_upload_bytes: int = 25 * 1024 * 1024
    cors_origins: list[str] = field(default_factory=list)
    # Where published App Studio sites are served. Use a separate domain in production,
    # e.g. https://sites.example.com (it must route /{site_key}/... to the API's /sites/...).
    sites_base_url: str = ""

    def validate(self) -> None:
        if self.env == "production" and self.jwt_secret == _DEV_SECRET:
            raise RuntimeError("NOVARA_JWT_SECRET must be set in production")
        if len(self.jwt_secret) < 32:
            raise RuntimeError("NOVARA_JWT_SECRET must be at least 32 characters")


def load_settings() -> Settings:
    env = os.environ
    settings = Settings(
        env=env.get("NOVARA_ENV", "development"),
        database_url=env.get("NOVARA_DATABASE_URL", Settings.database_url),
        jwt_secret=env.get("NOVARA_JWT_SECRET", _DEV_SECRET),
        session_ttl_minutes=int(env.get("NOVARA_SESSION_TTL_MINUTES", Settings.session_ttl_minutes)),
        storage_root=env.get("NOVARA_STORAGE_ROOT", Settings.storage_root),
        max_upload_bytes=int(env.get("NOVARA_MAX_UPLOAD_BYTES", Settings.max_upload_bytes)),
        cors_origins=[o for o in env.get("NOVARA_CORS_ORIGINS", "").split(",") if o],
        sites_base_url=env.get("NOVARA_SITES_BASE_URL", "").rstrip("/"),
    )
    settings.validate()
    return settings
