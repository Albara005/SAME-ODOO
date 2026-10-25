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
    # "local" (files under storage_root) or "s3" (any S3-compatible bucket: AWS S3, R2, MinIO...).
    storage_backend: str = "local"
    s3_endpoint: str = ""          # e.g. https://s3.me-central-1.amazonaws.com
    s3_bucket: str = ""
    s3_region: str = "us-east-1"   # "auto" for Cloudflare R2
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_prefix: str = ""            # optional folder inside the bucket
    max_upload_bytes: int = 25 * 1024 * 1024
    cors_origins: list[str] = field(default_factory=list)
    # Where published App Studio sites are served. Use a separate domain in production,
    # e.g. https://sites.example.com (it must route /{site_key}/... to the API's /sites/...).
    sites_base_url: str = ""
    # Where customers point their own domains (CNAME); defaults to the sites host.
    sites_cname_target: str = ""
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
        if self.storage_backend not in ("local", "s3"):
            raise RuntimeError("NOVARA_STORAGE_BACKEND must be 'local' or 's3'")
        if self.storage_backend == "s3":
            missing = [name for name, value in (("NOVARA_S3_ENDPOINT", self.s3_endpoint), ("NOVARA_S3_BUCKET", self.s3_bucket),
                                                ("NOVARA_S3_ACCESS_KEY_ID", self.s3_access_key_id),
                                                ("NOVARA_S3_SECRET_ACCESS_KEY", self.s3_secret_access_key)) if not value]
            if missing:
                raise RuntimeError(f"S3 storage needs {', '.join(missing)}")
            if self.env == "production" and not self.s3_endpoint.startswith("https://"):
                raise RuntimeError("NOVARA_S3_ENDPOINT must use https in production")


def load_settings() -> Settings:
    env = os.environ
    settings = Settings(
        env=env.get("NOVARA_ENV", "development"),
        database_url=env.get("NOVARA_DATABASE_URL", Settings.database_url),
        jwt_secret=env.get("NOVARA_JWT_SECRET", _DEV_SECRET),
        session_ttl_minutes=int(env.get("NOVARA_SESSION_TTL_MINUTES", Settings.session_ttl_minutes)),
        storage_root=env.get("NOVARA_STORAGE_ROOT", Settings.storage_root),
        storage_backend=env.get("NOVARA_STORAGE_BACKEND", Settings.storage_backend),
        s3_endpoint=env.get("NOVARA_S3_ENDPOINT", ""),
        s3_bucket=env.get("NOVARA_S3_BUCKET", ""),
        s3_region=env.get("NOVARA_S3_REGION", Settings.s3_region),
        s3_access_key_id=env.get("NOVARA_S3_ACCESS_KEY_ID", ""),
        s3_secret_access_key=env.get("NOVARA_S3_SECRET_ACCESS_KEY", ""),
        s3_prefix=env.get("NOVARA_S3_PREFIX", ""),
        max_upload_bytes=int(env.get("NOVARA_MAX_UPLOAD_BYTES", Settings.max_upload_bytes)),
        cors_origins=[o for o in env.get("NOVARA_CORS_ORIGINS", "").split(",") if o],
        sites_base_url=env.get("NOVARA_SITES_BASE_URL", "").rstrip("/"),
        sites_cname_target=env.get("NOVARA_SITES_CNAME_TARGET", "").strip().rstrip("."),
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
