"""First-run setup: the organization says what it does and what it wants to run, and the platform sets that up,
as a new database starts in Odoo.

    GET  /orgs/{org}/setup         the business types and apps to choose from, each type's suggested apps
    POST /orgs/{org}/setup         set up: the apps on the menu, the country (currency, VAT), sample data to
                                   explore with, and an online store on the catalog, published
    POST /orgs/{org}/setup/skip    every app on the menu, nothing created

Choosing apps only shapes the menu: every module stays available (and permissions still decide who may use
what), so turning an app on later from the organization's settings loses nothing. The setup runs once; a new
organization lands on it; organizations from before it that already hold records count as set up (migration
0053).

The store is an App Studio project from the store template: a public data key limited to the catalog and order
requests (with the proof-of-work check), its config.js pointed at this server, a first version, and that version
approved for production by the person setting up, who must hold what creating the key and deploying take.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara import crypto
from novara.db import utcnow
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.accounting.models import JournalEntry, Tax
from novara.modules.accounting.service import system_account
from novara.modules.audit import service as audit
from novara.modules.bookings.models import BookableService, BookingSettings, OpeningHours
from novara.modules.catalog.models import Product
from novara.modules.inventory import service as stock
from novara.modules.organizations.router import APPS, OrgOut, menu_apps
from novara.modules.partners.models import Customer
from novara.modules.projects.models import Project
from novara.money import CURRENCY_DIGITS
from novara.studio import service as studio
from novara.studio.models import AppDataKey, AppProject
from novara.studio.templates import store_config

router = APIRouter(tags=["organizations"])

BUSINESS_TYPES: dict[str, tuple[str, ...]] = {
    "store": ("customers", "products", "sales", "invoices", "inventory", "purchases", "accounting", "reports",
              "studio", "assistant"),
    "restaurant": ("customers", "products", "sales", "invoices", "inventory", "purchases", "accounting", "reports",
                   "hr", "payroll"),
    "services": ("customers", "products", "sales", "invoices", "projects", "accounting", "reports", "documents",
                 "assistant"),
    "appointments": ("customers", "products", "bookings", "invoices", "accounting", "reports", "hr", "studio"),
    "contracting": ("customers", "products", "sales", "invoices", "purchases", "projects", "hr", "payroll",
                    "accounting", "reports", "documents"),
    "trading": ("customers", "products", "sales", "invoices", "purchases", "inventory", "accounting", "reports", "hr"),
    "other": APPS,
}

# Country: its currency, standard VAT rate (0: no VAT set up) and the time zone bookings are taken in.
COUNTRIES: dict[str, tuple[str, Decimal, str]] = {
    "SA": ("SAR", Decimal(15), "Asia/Riyadh"),
    "AE": ("AED", Decimal(5), "Asia/Dubai"),
    "BH": ("BHD", Decimal(10), "Asia/Bahrain"),
    "OM": ("OMR", Decimal(5), "Asia/Muscat"),
    "KW": ("KWD", Decimal(0), "Asia/Kuwait"),
    "QA": ("QAR", Decimal(0), "Asia/Qatar"),
    "EG": ("EGP", Decimal(14), "Africa/Cairo"),
    "JO": ("JOD", Decimal(16), "Asia/Amman"),
}

# Sample data to explore with: (name, kind, price, description) per business type, and two customers for everyone.
# Sample products carry DEMO- SKUs, so they are easy to find and delete.
SAMPLE_PRODUCTS: dict[str, list[tuple[str, str, str, str]]] = {
    "store": [("قميص قطني", "goods", "79", "قطن ناعم بمقاسات من S إلى XXL"),
              ("حذاء رياضي", "goods", "249", "خفيف ومريح للمشي والجري"),
              ("حقيبة ظهر", "goods", "159", "مقاومة للماء بجيب للحاسوب المحمول"),
              ("ساعة يد", "goods", "399", "هيكل معدني وسوار جلدي")],
    "restaurant": [("برجر لحم", "goods", "32", "لحم بقري طازج مع الجبن والصوص الخاص"),
                   ("شاورما دجاج", "goods", "18", "بخبز الصاج مع الثوم والمخلل"),
                   ("عصير برتقال طازج", "goods", "12", "يُعصر عند الطلب"),
                   ("قهوة عربية", "goods", "10", "بالهيل والزعفران")],
    "services": [("ساعة استشارة", "service", "450", "جلسة مع مستشار مختص حضورية أو عن بعد"),
                 ("دراسة جدوى", "service", "8500", "دراسة سوق ومالية كاملة لمشروعك"),
                 ("تدريب يوم كامل", "service", "3200", "ورشة عمل لفريقك حتى 15 متدربًا")],
    "appointments": [("قص شعر", "service", "60", "قص وتصفيف"),
                     ("جلسة عناية", "service", "180", "عناية متكاملة بالبشرة"),
                     ("كشف عام", "service", "200", "كشف واستشارة")],
    "contracting": [("أعمال تشطيب (م²)", "service", "120", "دهان وجبس وأرضيات"),
                    ("إشراف هندسي (شهر)", "service", "15000", "مهندس مقيم وتقارير أسبوعية"),
                    ("توريد بلاط (م²)", "goods", "45", "بورسلين 60×60")],
    "trading": [("أرز بسمتي 10 كجم", "goods", "85", "حبة طويلة، كرتون 4 أكياس"),
                ("زيت دوار الشمس 1.5 لتر", "goods", "22", "كرتون 6 عبوات"),
                ("سكر 5 كجم", "goods", "28", "سكر أبيض ناعم")],
    "other": [("منتج تجريبي", "goods", "100", "منتج للتجربة"), ("خدمة تجريبية", "service", "250", "خدمة للتجربة")],
}
SAMPLE_CUSTOMERS = [
    {"name": "مؤسسة الأفق التجارية", "city": "الرياض", "email": "info@ufuq.example.com", "phone": "+966112345678"},
    {"name": "سارة القحطاني", "kind": "individual", "city": "جدة", "phone": "+966551234567"},
]
SAMPLE_MEMO = "بيانات تجريبية من الإعداد الأول"


class TypeOut(BaseModel):
    key: str
    apps: list[str]


class CountryOut(BaseModel):
    code: str
    currency: str
    vat_rate: Decimal                         # 0: no VAT set up


class SetupCatalogOut(BaseModel):
    business_types: list[TypeOut]
    apps: list[str]
    countries: list[CountryOut]
    completed: bool


class SetupIn(BaseModel):
    business_type: str = Field(max_length=30)
    apps: list[str] = Field(max_length=len(APPS))
    country: str = Field(default="SA", pattern="^[A-Z]{2}$")
    vat_number: str = Field(default="", max_length=20, pattern="^[0-9A-Z]*$")
    sample_data: bool = True
    store: bool = False                       # an online store on the catalog, published
    store_name: str = Field(default="", max_length=120)


class SetupOut(BaseModel):
    organization: OrgOut
    created: dict[str, int]
    store_url: str | None = None
    store_project_id: str | None = None
    notes: list[str] = Field(default_factory=list)   # currency_kept, store_not_permitted


def _vat(db: Session, rate: Decimal) -> tuple[Tax | None, bool]:
    """The standard VAT rate as a tax (the one there is, else a new one; True when new); none without VAT."""
    if rate <= 0:
        return None, False
    existing = next((t for t in db.scalars(select(Tax).where(Tax.active.is_(True))) if t.rate == rate), None)
    if existing is not None:
        return existing, False
    tax = Tax(name=f"ضريبة القيمة المضافة {rate.normalize():f}%", rate=rate,
              account_id=system_account(db, "vat_output").id)
    db.add(tax)
    db.flush()
    return tax, True


def _samples(db: Session, ctx: OrgContext, kind: str, apps: list[str], tax: Tax | None,
             timezone: str) -> dict[str, int]:
    created = {"products": 0, "customers": 0, "services": 0, "projects": 0, "stock": 0}
    products = []
    for n, (name, product_kind, price, description) in enumerate(SAMPLE_PRODUCTS[kind], start=1):
        p = Product(name=name, kind=product_kind, sale_price=Decimal(price), sku=f"DEMO-{n:02d}",
                    default_tax_id=tax.id if tax else None, description=description)
        db.add(p)
        products.append(p)
    db.flush()
    created["products"] = len(products)
    customers = [Customer(**c) for c in SAMPLE_CUSTOMERS]
    db.add_all(customers)
    db.flush()
    created["customers"] = len(customers)

    goods = [p for p in products if p.kind == "goods"]
    if goods and "inventory" in apps:
        # Opening stock, from the owner (equity), at 60% of the price.
        stock.receive(db, warehouse=stock.default_warehouse(db), move_date=date.today(),
                      currency=ctx.organization.default_currency, counterpart="equity", memo=SAMPLE_MEMO,
                      user_id=ctx.user.id,
                      lines=[(p, Decimal(25), (p.sale_price * Decimal("0.6")).quantize(Decimal("0.01"))) for p in goods])
        created["stock"] = len(goods)
    if "bookings" in apps:
        if db.scalar(select(BookingSettings.id)) is None:
            db.add(BookingSettings(timezone=timezone))
        if db.scalar(select(OpeningHours.id).limit(1)) is None:
            # Saturday to Thursday, 9 in the morning to 9 at night (Monday = 0).
            db.add_all(OpeningHours(weekday=d, opens_at="09:00", closes_at="21:00") for d in (5, 6, 0, 1, 2, 3))
        for p in products:
            if p.kind == "service":
                db.add(BookableService(name=p.name, duration_minutes=60 if p.sale_price >= 100 else 30,
                                       price=p.sale_price, description=p.description))
                created["services"] += 1
    if "projects" in apps:
        db.add(Project(name="مشروع تجريبي", description=SAMPLE_MEMO, customer_id=customers[0].id,
                       hourly_rate=Decimal(300), tax_id=tax.id if tax else None, budget_hours=Decimal(40),
                       manager_user_id=ctx.user.id, created_by=ctx.user.id))
        created["projects"] = 1
    db.flush()
    return created


STORE_NEEDS = (P.APPS_WRITE, P.APPS_DEPLOY, P.PRODUCTS_READ, P.SALES_WRITE)


def _store(db: Session, ctx: OrgContext, request: Request, name: str) -> tuple[str, str]:
    """An online store from the store template, its data key in config.js, published. Returns (url, project id)."""
    storage, settings = request.app.state.storage, request.app.state.settings
    slug, n = "store", 1
    while db.scalar(select(AppProject.id).where(AppProject.slug == slug)):
        n += 1
        slug = f"store-{n}"
    project = studio.create_project(db, storage, name=name, slug=slug, template_key="store",
                                    description="متجرنا على الإنترنت", user_id=ctx.user.id)
    raw = "nvpk_" + secrets.token_urlsafe(24)
    db.add(AppDataKey(project_id=project.id, name="المتجر", key_hash=crypto.token_hash(raw), hint=raw[:12],
                      scopes=["catalog", "orders"], created_by=ctx.user.id, require_proof=True))
    # The store calls this server, at the address the person setting it up reached it by.
    base = str(request.base_url).rstrip("/")
    studio.ProjectFiles(storage, project).write("config.js", store_config(base, raw).encode())
    version = studio.cut_version(db, storage, project, message="المتجر كما جهّزه الإعداد الأول", user_id=ctx.user.id)
    deployment = studio.request_production(db, project, version, user_id=ctx.user.id)
    studio.approve_production(db, project, deployment, approver_id=ctx.user.id)
    sites = settings.sites_base_url or base + "/sites"
    return f"{sites}/{deployment.site_key}/", project.id


@router.get("/orgs/{org_id}/setup", response_model=SetupCatalogOut)
def setup_catalog(ctx: OrgContext = Depends(require(P.ORG_MANAGE))):
    return SetupCatalogOut(business_types=[TypeOut(key=k, apps=list(v)) for k, v in BUSINESS_TYPES.items()],
                           apps=list(APPS),
                           countries=[CountryOut(code=k, currency=c, vat_rate=r) for k, (c, r, _) in COUNTRIES.items()],
                           completed=ctx.organization.setup_completed_at is not None)


@router.post("/orgs/{org_id}/setup", response_model=SetupOut)
def run_setup(body: SetupIn, request: Request, ctx: OrgContext = Depends(require(P.ORG_MANAGE)),
              db: Session = Depends(get_db)):
    org = ctx.organization
    if org.setup_completed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "this organization is already set up; change its apps from "
                                                      "the organization's settings")
    if body.business_type not in BUSINESS_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown business type: {body.business_type}")
    if body.country not in COUNTRIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unsupported country: {body.country}")
    apps = menu_apps([*body.apps, *(("products", "sales", "studio") if body.store else ())])
    if not apps:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "choose at least one app")
    notes: list[str] = []
    currency, rate, timezone = COUNTRIES[body.country]
    if currency != org.default_currency:
        if currency in CURRENCY_DIGITS and db.scalar(select(JournalEntry.id).limit(1)) is None:
            org.default_currency = currency
        else:
            notes.append("currency_kept")                   # the books already have entries in the old one
    if body.vat_number:
        org.vat_number = body.vat_number
    tax, new_tax = _vat(db, rate)
    created = {"taxes": int(new_tax)}
    if body.sample_data:
        created |= _samples(db, ctx, body.business_type, apps, tax, timezone)
    store_url = store_project = None
    if body.store:
        missing = [p for p in STORE_NEEDS if not ctx.has(p)]
        if missing:
            notes.append("store_not_permitted")             # creating the key and deploying take more
        else:
            store_url, store_project = _store(db, ctx, request, body.store_name.strip() or org.name)
    org.business_type, org.apps, org.setup_completed_at = body.business_type, apps, utcnow()
    audit.record(db, actor_user_id=ctx.user.id, action="org.setup_completed", target_type="organization",
                 target_id=org.id, details={"business_type": body.business_type, "apps": apps,
                                            "country": body.country, "sample_data": body.sample_data,
                                            "store": store_project is not None}, ip_address=client_ip(request))
    db.commit()
    return SetupOut(organization=OrgOut.model_validate(org), created=created, store_url=store_url,
                    store_project_id=store_project, notes=notes)


@router.post("/orgs/{org_id}/setup/skip", response_model=OrgOut)
def skip_setup(request: Request, ctx: OrgContext = Depends(require(P.ORG_MANAGE)), db: Session = Depends(get_db)):
    org = ctx.organization
    if org.setup_completed_at is None:
        org.setup_completed_at = utcnow()
        audit.record(db, actor_user_id=ctx.user.id, action="org.setup_skipped", target_type="organization",
                     target_id=org.id, details={}, ip_address=client_ip(request))
        db.commit()
    return org
