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
    # Live updates (server-sent events): how often the server checks for changes, and how long one
    # stream stays open before the client reconnects (keeps proxies and stale connections in check).
    live_interval_seconds: float = 3.0
    live_max_seconds: int = 300
    # Links in emails (verification, password reset, invitations) point here.
    app_base_url: str = "http://localhost:5173"
    # Outgoing email. Without an SMTP host, emails are written to the log (development).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    mail_from: str = "NOVARA <no-reply@localhost>"
    # Key for secrets stored encrypted in the database (two-factor secrets). Defaults to one
    # derived from the JWT secret; set it separately in production so the two can rotate apart.
    encryption_key: str = ""

    def validate(self) -> None:
        if self.env == "production" and self.jwt_secret == _DEV_SECRET:
            raise RuntimeError("NOVARA_JWT_SECRET must be set in production")
        if len(self.jwt_secret) < 32:
            raise RuntimeError("NOVARA_JWT_SECRET must be at least 32 characters")
        if self.env == "production" and len(self.encryption_key) < 32:
            raise RuntimeError("NOVARA_ENCRYPTION_KEY must be set (at least 32 characters) in production")


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
        live_interval_seconds=float(env.get("NOVARA_LIVE_INTERVAL_SECONDS", Settings.live_interval_seconds)),
        live_max_seconds=int(env.get("NOVARA_LIVE_MAX_SECONDS", Settings.live_max_seconds)),
        app_base_url=env.get("NOVARA_APP_BASE_URL", Settings.app_base_url).rstrip("/"),
        smtp_host=env.get("NOVARA_SMTP_HOST", ""),
        smtp_port=int(env.get("NOVARA_SMTP_PORT", Settings.smtp_port)),
        smtp_user=env.get("NOVARA_SMTP_USER", ""),
        smtp_password=env.get("NOVARA_SMTP_PASSWORD", ""),
        smtp_starttls=env.get("NOVARA_SMTP_STARTTLS", "true").lower() != "false",
        mail_from=env.get("NOVARA_MAIL_FROM", Settings.mail_from),
        encryption_key=env.get("NOVARA_ENCRYPTION_KEY", ""),
    )
    settings.validate()
    return settings
