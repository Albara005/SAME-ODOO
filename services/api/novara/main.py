"""Application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import text

from novara.ai.provider import AnthropicProvider, ModelProvider
from novara.mail import Mailer, mailer_from_settings
from novara.ai.router import router as assistant_router
from novara.agents.router import router as agents_router
from novara.automation.inbound import public_router as inbound_hooks_router
from novara.automation.live import router as live_router
from novara.automation.router import router as automation_router
from novara.automation.webhooks import WebhookSender
from novara.config import Settings, load_settings
from novara.creative.router import router as creative_router
from novara.hosting.router import router as hosting_router
from novara.studio.data_keys import public_router as public_data_router, router as data_keys_router
from novara.studio import domains
from novara.studio.router import router as studio_router, serve_custom_domain, sites_router
from novara.db import Base, make_engine, make_sessionmaker
from novara.modules.access.router import router as access_router
from novara.modules.accounting.router import router as accounting_router
from novara.modules.accounting.service import PeriodLocked
from novara.modules.catalog.router import router as catalog_router
from novara.modules.inventory.router import router as inventory_router
from novara.modules.bookings.router import router as bookings_router
from novara.modules.hr.router import router as hr_router
from novara.modules.identity.passkeys import router as passkeys_router
from novara.modules.invoicing.credit_router import router as credit_router
from novara.modules.invoicing.router import router as invoicing_router
from novara.modules.partners.router import router as partners_router
from novara.modules.payroll.router import router as payroll_router
from novara.modules.projects.router import router as projects_router
from novara.modules.purchasing.credit_router import router as vendor_credit_router
from novara.modules.purchasing.router import router as purchasing_router
from novara.modules.sales.router import router as sales_router
from novara.modules.sales.web_orders import router as web_orders_router
from novara.modules.audit.router import router as audit_router
from novara.modules.dataio.router import router as dataio_router
from novara.modules.files.router import router as files_router
from novara.modules.files.storage import ObjectStorage, storage_from_settings
from novara.modules.identity.router import router as identity_router
from novara.modules.organizations.router import router as organizations_router
from novara.payments.router import public_router as public_payments_router, router as payments_router
from novara.einvoice.router import router as einvoice_router
from novara import push
from novara.studio import browser as studio_browser

# Import models so they are registered on Base.metadata.
from novara.modules.access import models as _access_models  # noqa: F401
from novara.modules.accounting import models as _accounting_models  # noqa: F401
from novara.modules.catalog import models as _catalog_models  # noqa: F401
from novara.modules.inventory import models as _inventory_models  # noqa: F401
from novara.modules.invoicing import models as _invoicing_models  # noqa: F401
from novara.modules.partners import models as _partner_models  # noqa: F401
from novara.modules.payroll import models as _payroll_models  # noqa: F401
from novara.modules.sales import models as _sales_models  # noqa: F401
from novara.modules.purchasing import models as _purchasing_models  # noqa: F401
from novara import idempotency as _idempotency_models  # noqa: F401
from novara.ai import models as _ai_models  # noqa: F401
from novara.studio import models as _studio_models  # noqa: F401
from novara.agents import models as _agent_models  # noqa: F401
from novara.automation import events as _automation_events  # noqa: F401  (subscribes to the audit trail)
from novara.automation import models as _automation_models  # noqa: F401
from novara.creative import models as _creative_models  # noqa: F401
from novara.hosting import models as _hosting_models  # noqa: F401
from novara.modules.audit import models as _audit_models  # noqa: F401
from novara.modules.files import models as _files_models  # noqa: F401
from novara.modules.identity import models as _identity_models  # noqa: F401
from novara.modules.organizations import models as _org_models  # noqa: F401
from novara.payments import models as _payment_models  # noqa: F401
from novara.einvoice import models as _einvoice_models  # noqa: F401


def create_app(settings: Settings | None = None, storage: ObjectStorage | None = None,
               create_schema: bool | None = None, ai_provider: ModelProvider | None = None,
               mailer: Mailer | None = None) -> FastAPI:
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
    app.state.storage = storage or storage_from_settings(settings)
    # Claude via the Anthropic SDK; credentials come from the environment when first used.
    app.state.ai_provider = ai_provider or AnthropicProvider()
    # SMTP when NOVARA_SMTP_HOST is set; otherwise emails go to the log (development).
    app.state.mailer = mailer or mailer_from_settings(settings)
    # Workflow webhooks: signed, public internet only (automation/webhooks.py).
    app.state.webhooks = WebhookSender(settings)
    # Calls to payment providers (Stripe); None opens a client per call. Tests put a mock transport here.
    app.state.payment_http = None
    # Calls to ZATCA's Fatoora API (e-invoicing); None opens a client per call. Tests put a mock transport here.
    app.state.einvoice_http = None
    # Web Push to members' devices: every notification goes out after its transaction commits.
    app.state.push = push.PushSender(settings, app.state.sessionmaker)
    push.configure(app.state.push)
    # App Studio: each version's pages loaded in headless Chromium (when enabled and installed).
    studio_browser.configure(studio_browser.BrowserChecker(settings))

    @app.exception_handler(PeriodLocked)
    def closed_books(_request: Request, exc: PeriodLocked) -> JSONResponse:
        # Any posting (invoice, payment, stock move...) dated in closed books.
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    for r in (identity_router, passkeys_router, organizations_router, access_router, audit_router, files_router,
              # dataio before the resource routers: /customers/export.csv must not match /customers/{id}.
              dataio_router, partners_router, catalog_router, accounting_router, invoicing_router, credit_router,
              inventory_router, sales_router, web_orders_router, purchasing_router, vendor_credit_router, projects_router, hr_router, payroll_router, bookings_router, assistant_router, studio_router, agents_router, automation_router,
              creative_router, hosting_router, live_router, data_keys_router, public_data_router, inbound_hooks_router,
              payments_router, public_payments_router, einvoice_router, push.router):
        app.include_router(r, prefix="/api/v1")
    app.include_router(sites_router)  # public, unauthenticated: published App Studio sites

    # Customers' own domains (studio/domains.py): a request for a proven domain gets that site. Every other
    # host, the platform's own included, goes through to the routes above.
    platform_hosts = domains.own_hosts(settings)
    app.state.domain_lookup = domains.txt_records  # tests replace the DNS lookup

    @app.middleware("http")
    async def custom_domains(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip().lower().rstrip(".")
        if request.method in ("GET", "HEAD") and host and host not in platform_hosts and "." in host \
                and not host.replace(".", "").isdigit() and not host.startswith("["):
            response = await run_in_threadpool(serve_custom_domain, request.app, host, request.url.path)
            if response is not None:
                return response
        return await call_next(request)

    return app
