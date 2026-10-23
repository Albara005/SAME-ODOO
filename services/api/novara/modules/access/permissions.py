"""Permission catalog and the default roles every organization starts with.

Permissions are checked on the server for every sensitive operation; hiding
a button in the UI is never treated as protection.

System roles take their permissions from this file, not from the database,
so every organization picks up new permissions when the catalog grows.
Custom roles store their own permission list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novara.modules.access.models import Role

ORG_MANAGE = "org.manage"
MEMBERS_READ = "members.read"
MEMBERS_MANAGE = "members.manage"
ROLES_MANAGE = "roles.manage"
AUDIT_READ = "audit.read"
FILES_READ = "files.read"
FILES_WRITE = "files.write"
FILES_DELETE = "files.delete"
CUSTOMERS_READ = "customers.read"
CUSTOMERS_WRITE = "customers.write"
PRODUCTS_READ = "products.read"
PRODUCTS_WRITE = "products.write"
TAXES_MANAGE = "taxes.manage"
INVOICES_READ = "invoices.read"
INVOICES_WRITE = "invoices.write"
INVOICES_POST = "invoices.post"
INVOICES_CANCEL = "invoices.cancel"
INVOICES_CREDIT = "invoices.credit"
PAYMENTS_WRITE = "payments.write"
ACCOUNTING_READ = "accounting.read"
ACCOUNTING_CLOSE = "accounting.close"
ACCOUNTING_JOURNAL = "accounting.journal"
PURCHASES_READ = "purchases.read"
PURCHASES_WRITE = "purchases.write"
BILLS_POST = "bills.post"
SALES_READ = "sales.read"
SALES_WRITE = "sales.write"
INVENTORY_READ = "inventory.read"
INVENTORY_WRITE = "inventory.write"
AI_USE = "ai.use"
AI_MANAGE = "ai.manage"
APPS_READ = "apps.read"
APPS_WRITE = "apps.write"
APPS_DEPLOY = "apps.deploy"
AGENTS_RUN = "agents.run"
AGENTS_MANAGE = "agents.manage"
WORKFLOWS_MANAGE = "workflows.manage"
BRAND_READ = "brand.read"
BRAND_MANAGE = "brand.manage"
HOSTING_MANAGE = "hosting.manage"
PROJECTS_READ = "projects.read"
PROJECTS_MANAGE = "projects.manage"
TIMESHEETS_WRITE = "timesheets.write"
HR_READ = "hr.read"
HR_MANAGE = "hr.manage"
LEAVE_REQUEST = "leave.request"

CATALOG: dict[str, str] = {
    ORG_MANAGE: "Edit organization settings",
    MEMBERS_READ: "View members",
    MEMBERS_MANAGE: "Add, suspend and remove members, change their roles",
    ROLES_MANAGE: "Create and edit roles",
    AUDIT_READ: "Read the audit log",
    FILES_READ: "View and download files",
    FILES_WRITE: "Upload files",
    FILES_DELETE: "Delete files",
    CUSTOMERS_READ: "View customers",
    CUSTOMERS_WRITE: "Create, edit and archive customers",
    PRODUCTS_READ: "View products",
    PRODUCTS_WRITE: "Create, edit and archive products",
    TAXES_MANAGE: "Create and edit tax rates",
    INVOICES_READ: "View invoices",
    INVOICES_WRITE: "Create and edit draft invoices",
    INVOICES_POST: "Post invoices to the ledger (makes them final)",
    INVOICES_CANCEL: "Cancel posted invoices with a reversing entry",
    INVOICES_CREDIT: "Issue credit notes (and take back returned goods) against posted invoices",
    PAYMENTS_WRITE: "Register customer payments, refunds and payments to vendors",
    ACCOUNTING_READ: "View the chart of accounts, journal and financial reports",
    ACCOUNTING_CLOSE: "Close the books up to a date, and close the year into retained earnings",
    ACCOUNTING_JOURNAL: "Post and reverse manual journal entries, add and archive accounts",
    PURCHASES_READ: "View vendors, purchase orders and vendor bills",
    PURCHASES_WRITE: "Create vendors, purchase orders and draft vendor bills",
    BILLS_POST: "Post and cancel vendor bills",
    SALES_READ: "View quotations and sales orders",
    SALES_WRITE: "Create, confirm and cancel quotations and sales orders",
    INVENTORY_READ: "View stock levels, moves and valuation",
    INVENTORY_WRITE: "Receive, deliver and adjust stock",
    AI_USE: "Use the AI assistant (it can only do what the user is allowed to do)",
    AI_MANAGE: "Enable or disable the AI assistant, set its budget and models, view AI usage",
    APPS_READ: "View App Studio projects, versions and deployments",
    APPS_WRITE: "Create and edit App Studio projects, cut versions, publish previews",
    APPS_DEPLOY: "Approve production deployments and roll them back",
    AGENTS_RUN: "Run published agents (with your own permissions)",
    AGENTS_MANAGE: "Create, configure, test and publish agents; switch them off",
    WORKFLOWS_MANAGE: "Create and manage automation workflows, see all tasks",
    BRAND_READ: "View brand kits, logos and design tokens",
    BRAND_MANAGE: "Create and edit brand kits and logos, use the design agent",
    HOSTING_MANAGE: "Request dedicated hosting (an owner approves the cost)",
    PROJECTS_READ: "View projects, their tasks and the hours logged on them",
    PROJECTS_MANAGE: "Create and change projects and their tasks, log anyone's hours, invoice billable hours",
    TIMESHEETS_WRITE: "Log your own hours on projects",
    HR_READ: "View the employee directory and departments",
    HR_MANAGE: "Add and edit employee records, departments, leave types, the weekend and public holidays; decide anyone's leave",
    LEAVE_REQUEST: "Ask for your own leave and see your balance",
}

OWNER_ROLE = "owner"

_SALES = [CUSTOMERS_READ, CUSTOMERS_WRITE, PRODUCTS_READ, INVOICES_READ, INVOICES_WRITE,
          SALES_READ, SALES_WRITE, INVENTORY_READ]
_ACCOUNTING = [
    CUSTOMERS_READ, PRODUCTS_READ, INVOICES_READ, INVOICES_POST, INVOICES_CANCEL, INVOICES_CREDIT,
    PAYMENTS_WRITE, ACCOUNTING_READ, ACCOUNTING_CLOSE, ACCOUNTING_JOURNAL, TAXES_MANAGE, SALES_READ, INVENTORY_READ,
    PURCHASES_READ, BILLS_POST,
]
# Receiving goods against purchase orders is warehouse work.
_WAREHOUSE = [PRODUCTS_READ, SALES_READ, INVENTORY_READ, INVENTORY_WRITE, PURCHASES_READ]
# Every employee role sees the directory and asks for its own leave.
_PEOPLE = [HR_READ, LEAVE_REQUEST]

SYSTEM_ROLES: dict[str, tuple[str, list[str]]] = {
    OWNER_ROLE: ("Owner", sorted(CATALOG)),
    "admin": ("Administrator", sorted(set(CATALOG) - {ORG_MANAGE})),
    "accountant": ("Accountant", sorted({MEMBERS_READ, FILES_READ, FILES_WRITE, AI_USE, AGENTS_RUN, PROJECTS_READ,
                                         *_ACCOUNTING, *_PEOPLE})),
    "sales": ("Sales", sorted({MEMBERS_READ, FILES_READ, FILES_WRITE, AI_USE, AGENTS_RUN, PROJECTS_READ, TIMESHEETS_WRITE,
                               *_SALES, *_PEOPLE})),
    "warehouse": ("Warehouse", sorted({MEMBERS_READ, FILES_READ, AI_USE, AGENTS_RUN, *_WAREHOUSE, *_PEOPLE})),
    # Builds apps and publishes previews; production needs someone with apps.deploy.
    "developer": ("Developer", sorted({MEMBERS_READ, FILES_READ, FILES_WRITE, AI_USE, AGENTS_RUN, BRAND_READ, APPS_READ, APPS_WRITE,
                                       PROJECTS_READ, TIMESHEETS_WRITE, *_PEOPLE})),
    "member": ("Member", sorted([AGENTS_RUN, AI_USE, CUSTOMERS_READ, FILES_READ, FILES_WRITE, MEMBERS_READ, PRODUCTS_READ,
                                 PROJECTS_READ, TIMESHEETS_WRITE, *_PEOPLE])),
    "viewer": ("Viewer", [FILES_READ]),
}


def effective_permissions(role: Role) -> set[str]:
    if role.is_system and role.key in SYSTEM_ROLES:
        return set(SYSTEM_ROLES[role.key][1])
    return set(role.permissions)


def unknown_permissions(perms: list[str]) -> list[str]:
    return [p for p in perms if p not in CATALOG]
