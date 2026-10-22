"""Application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from novara.ai.provider import AnthropicProvider, ModelProvider
from novara.ai.router import router as assistant_router
from novara.config import Settings, load_settings
from novara.studio.router import router as studio_router, sites_router
from novara.db import Base, make_engine, make_sessionmaker
from novara.modules.access.router import router as access_router
from novara.modules.accounting.router import router as accounting_router
from novara.modules.catalog.router import router as catalog_router
from novara.modules.inventory.router import router as inventory_router
from novara.modules.invoicing.router import router as invoicing_router
from novara.modules.partners.router import router as partners_router
from novara.modules.sales.router import router as sales_router
from novara.modules.audit.router import router as audit_router
from novara.modules.dataio.router import router as dataio_router
from novara.modules.files.router import router as files_router
from novara.modules.files.storage import LocalObjectStorage, ObjectStorage
from novara.modules.identity.router import router as identity_router
from novara.modules.organizations.router import router as organizations_router

# Import models so they are registered on Base.metadata.
from novara.modules.access import models as _access_models  # noqa: F401
from novara.modules.accounting import models as _accounting_models  # noqa: F401
from novara.modules.catalog import models as _catalog_models  # noqa: F401
from novara.modules.inventory import models as _inventory_models  # noqa: F401
from novara.modules.invoicing import models as _invoicing_models  # noqa: F401
from novara.modules.partners import models as _partner_models  # noqa: F401
from novara.modules.sales import models as _sales_models  # noqa: F401
from novara import idempotency as _idempotency_models  # noqa: F401
from novara.ai import models as _ai_models  # noqa: F401
from novara.studio import models as _studio_models  # noqa: F401
from novara.modules.audit import models as _audit_models  # noqa: F401
from novara.modules.files import models as _files_models  # noqa: F401
from novara.modules.identity import models as _identity_models  # noqa: F401
from novara.modules.organizations import models as _org_models  # noqa: F401


def create_app(settings: Settings | None = None, storage: ObjectStorage | None = None,
               create_schema: bool | None = None, ai_provider: ModelProvider | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.validate()
    engine = make_engine(settings.database_url)
    if create_schema is None:
        create_schema = engine.dialect.name == "sqlite" and settings.env != "production"
    if create_schema:
        # Local SQLite/tests only. PostgreSQL schema changes go through migrations/ (python -m novara.migrate).
        Base.metadata.create_all(engine)

    app = FastAPI(title="NOVARA Platform API", version="0.1.0")
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.storage = storage or LocalObjectStorage(settings.storage_root)
    # Claude via the Anthropic SDK; credentials come from the environment when first used.
    app.state.ai_provider = ai_provider or AnthropicProvider()

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    for r in (identity_router, organizations_router, access_router, audit_router, files_router,
              # dataio before the resource routers: /customers/export.csv must not match /customers/{id}.
              dataio_router, partners_router, catalog_router, accounting_router, invoicing_router,
              inventory_router, sales_router, assistant_router, studio_router):
        app.include_router(r, prefix="/api/v1")
    app.include_router(sites_router)  # public, unauthenticated: published App Studio sites
    return app
