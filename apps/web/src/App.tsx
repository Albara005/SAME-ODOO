import { Component, lazy, Suspense, type ComponentType, type ErrorInfo, type ReactNode } from "react";
import { Navigate, Outlet, Route, Routes, useLocation, useParams } from "react-router-dom";
import { useAuth } from "./auth/Auth";
import { lastOrg, OrgProvider, rememberOrg, useOrgQuery } from "./auth/Org";
import { Layout } from "./components/Layout";
import { Button, ErrorNotice, Loading, Notice } from "./components/ui";
import { useI18n } from "./i18n/I18n";
import { reloadForNewRelease } from "./lib/release";
import {
  ForgotPasswordPage, InvitePage, LoginPage, MfaRequiredPage, RegisterPage, ResetPasswordPage, VerifyEmailPage,
} from "./pages/Auth";
import { HomePage } from "./pages/Home";
import { OrgsPage, useMyOrgs } from "./pages/Orgs";

// Each area loads when first opened, so signing in and the home page stay light. If its code is gone, a release
// replaced it since this tab opened: reload once for the new one (meanwhile the page keeps showing it is loading).
function page<K extends string, M extends Record<K, ComponentType>>(load: () => Promise<M>, name: K) {
  return lazy(() =>
    load()
      .then((m) => ({ default: m[name] }))
      .catch((e: unknown) => {
        if (reloadForNewRelease()) return new Promise<never>(() => {});
        throw e;
      }),
  );
}
const AccountPage = page(() => import("./pages/Account"), "AccountPage");
const admin = () => import("./pages/Admin");
const AuditPage = page(admin, "AuditPage");
const MembersPage = page(admin, "MembersPage");
const OrgSettingsPage = page(admin, "OrgSettingsPage");
const RolesPage = page(admin, "RolesPage");
const agents = () => import("./pages/Agents");
const AgentPage = page(agents, "AgentPage");
const AgentsPage = page(agents, "AgentsPage");
const AiSettingsPage = page(() => import("./pages/AiSettings"), "AiSettingsPage");
const AssistantPage = page(() => import("./pages/Assistant"), "AssistantPage");
const automation = () => import("./pages/Automation");
const AutomationPage = page(automation, "AutomationPage");
const WorkflowEditorPage = page(automation, "WorkflowEditorPage");
const WorkflowPage = page(automation, "WorkflowPage");
const creative = () => import("./pages/Creative");
const BrandKitPage = page(creative, "BrandKitPage");
const CreativePage = page(creative, "CreativePage");
const CreditNotePage = page(() => import("./pages/CreditNotes"), "CreditNotePage");
const CustomersPage = page(() => import("./pages/Customers"), "CustomersPage");
const DocumentsPage = page(() => import("./pages/Documents"), "DocumentsPage");
const HostingPage = page(() => import("./pages/Hosting"), "HostingPage");
const invoices = () => import("./pages/Invoices");
const InvoiceDetailPage = page(invoices, "InvoiceDetailPage");
const InvoiceEditorPage = page(invoices, "InvoiceEditorPage");
const DebitNoteEditorPage = page(invoices, "DebitNoteEditorPage");
const InvoicesPage = page(invoices, "InvoicesPage");
const InventoryPage = page(() => import("./pages/Inventory"), "InventoryPage");
const ProductsPage = page(() => import("./pages/Products"), "ProductsPage");
const purchases = () => import("./pages/Purchases");
const BillDetailPage = page(purchases, "BillDetailPage");
const BillEditorPage = page(purchases, "BillEditorPage");
const PurchaseOrderDetailPage = page(purchases, "PurchaseOrderDetailPage");
const PurchaseOrderEditorPage = page(purchases, "PurchaseOrderEditorPage");
const PurchasesPage = page(purchases, "PurchasesPage");
const VendorCreditPage = page(() => import("./pages/VendorCredits"), "VendorCreditPage");
const AccountingPage = page(() => import("./pages/Accounting"), "AccountingPage");
const projects = () => import("./pages/Projects");
const ProjectsPage = page(projects, "ProjectsPage");
const ProjectWorkPage = page(projects, "ProjectPage");
const HrPage = page(() => import("./pages/Hr"), "HrPage");
const PayrollPage = page(() => import("./pages/Payroll"), "PayrollPage");
const BookingsPage = page(() => import("./pages/Bookings"), "BookingsPage");
const ReportsPage = page(() => import("./pages/Reports"), "ReportsPage");
const SetupPage = page(() => import("./pages/Setup"), "SetupPage");
const sales = () => import("./pages/Sales");
const SalesOrderDetailPage = page(sales, "SalesOrderDetailPage");
const SalesOrderEditorPage = page(sales, "SalesOrderEditorPage");
const SalesOrdersPage = page(sales, "SalesOrdersPage");
const studio = () => import("./pages/Studio");
const ProjectPage = page(studio, "ProjectPage");
const StudioPage = page(studio, "StudioPage");
const work = () => import("./pages/Work");
const ApprovalsPage = page(work, "ApprovalsPage");
const NotificationsPage = page(work, "NotificationsPage");
const TasksPage = page(work, "TasksPage");

/** A page that throws while rendering: say so, offer a reload, and keep what went wrong for whoever reports it. */
type Crash = { error: Error; where: string };
class ErrorBoundary extends Component<{ children: ReactNode; path: string }, { crash: Crash | null; path: string }> {
  state = { crash: null as Crash | null, path: "" };
  static getDerivedStateFromError(error: Error) {
    return { crash: { error, where: "" } };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    // The first components on the way down to the one that threw, for the report (the bundle's source maps
    // turn their file:line:column back into the source).
    const where = (info.componentStack ?? "").trim().split("\n").slice(0, 4).map((l) => l.trim()).join("\n");
    this.setState({ crash: { error, where }, path: this.props.path });
    console.error("NOVARA page error", error, info.componentStack);
  }
  componentDidUpdate() {
    // Going to another page (the browser's back button) tries again there.
    if (this.state.crash && this.state.path && this.props.path !== this.state.path) this.setState({ crash: null, path: "" });
  }
  render() {
    return this.state.crash ? <Crashed crash={this.state.crash} path={this.state.path || this.props.path} /> : this.props.children;
  }
}

function Crashed({ crash, path }: { crash: Crash; path: string }) {
  const { t } = useI18n();
  const { error, where } = crash;
  const stack = (error.stack ?? "").split("\n").filter((l) => l.includes("/assets/")).slice(0, 4).map((l) => l.trim());
  return (
    <div className="content" style={{ maxWidth: 760 }}>
      <div className="notice notice-error stack-sm" role="alert">
        <span>{t("error.boundary")}</span>
        <div className="row">
          <Button size="sm" variant="primary" onClick={() => window.location.reload()}>{t("error.reload")}</Button>
          <Button size="sm" variant="ghost" onClick={() => window.location.assign("/")}>{t("error.home")}</Button>
        </div>
        <details className="small">
          <summary>{t("error.details")}</summary>
          <pre className="crash-details" dir="ltr">
            {[`${error.name}: ${error.message}`, `page: ${path.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ":id")}`, ...stack, where]
              .filter(Boolean).join("\n")}
          </pre>
        </details>
      </div>
    </div>
  );
}

function RequireAuth() {
  const { user, loading, signedOut } = useAuth();
  const location = useLocation();
  if (loading) return <Loading />;
  // After an expired session, come back here once signed in. After signing out, whoever signs in next starts at home.
  if (!user) return <Navigate to="/login" replace state={signedOut ? null : { from: location.pathname + location.search }} />;
  return <Outlet />;
}

function GuestOnly() {
  const { user, loading } = useAuth();
  const from = (useLocation().state as { from?: string } | null)?.from;
  if (loading) return <Loading />;
  return user ? <Navigate to={from ?? "/"} replace /> : <Outlet />;
}

function RootRedirect() {
  const orgs = useMyOrgs();
  if (orgs.isPending) return <Loading />;
  const list = orgs.data ?? [];
  const saved = lastOrg();
  const target = list.find((o) => o.id === saved) ?? (list.length === 1 ? list[0] : undefined);
  return <Navigate to={target ? `/o/${target.id}` : "/orgs"} replace />;
}

/** An organization's pages, in the app's shell; the first-run setup (`setup`) has the whole screen. */
function OrgRoute({ setup = false }: { setup?: boolean }) {
  const { orgId = "" } = useParams();
  const { user } = useAuth();
  const org = useOrgQuery(orgId);
  if (org.isPending) return <Loading />;
  if (org.isError) return <Navigate to="/orgs" replace />;
  if (org.data.require_mfa && !user?.two_step) return <MfaRequiredPage orgName={org.data.name} hasPasskey={user?.has_passkey ?? false} />;
  rememberOrg(org.data.id);
  return (
    <OrgProvider org={org.data}>
      {setup ? (
        <Suspense fallback={<Loading />}>
          <SetupPage />
        </Suspense>
      ) : (
        <Layout />
      )}
    </OrgProvider>
  );
}

function NotFound() {
  const { t } = useI18n();
  return <ErrorNotice error={new Notice(t("error.notFound"))} />;
}

export function App() {
  const { pathname } = useLocation();
  return (
    <ErrorBoundary path={pathname}>
      <Routes>
        <Route element={<GuestOnly />}>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        </Route>
        {/* Links from emails: they work signed in or not. */}
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/invite" element={<InvitePage />} />
        <Route element={<RequireAuth />}>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/orgs" element={<OrgsPage />} />
          <Route
            path="/account"
            element={
              <Suspense fallback={<Loading />}>
                <AccountPage />
              </Suspense>
            }
          />
          <Route path="/o/:orgId/setup" element={<OrgRoute setup />} />
          <Route path="/o/:orgId" element={<OrgRoute />}>
            <Route index element={<HomePage />} />
            <Route path="customers" element={<CustomersPage />} />
            <Route path="products" element={<ProductsPage />} />
            <Route path="invoices" element={<InvoicesPage />} />
            <Route path="invoices/new" element={<InvoiceEditorPage />} />
            <Route path="invoices/:invoiceId" element={<InvoiceDetailPage />} />
            <Route path="invoices/:invoiceId/edit" element={<InvoiceEditorPage />} />
            <Route path="invoices/:invoiceId/debit-note" element={<DebitNoteEditorPage />} />
            <Route path="sales" element={<SalesOrdersPage />} />
            <Route path="sales/new" element={<SalesOrderEditorPage />} />
            <Route path="sales/:orderId" element={<SalesOrderDetailPage />} />
            <Route path="sales/:orderId/edit" element={<SalesOrderEditorPage />} />
            <Route path="credit-notes/:noteId" element={<CreditNotePage />} />
            <Route path="purchases" element={<PurchasesPage />} />
            <Route path="purchases/orders/new" element={<PurchaseOrderEditorPage />} />
            <Route path="purchases/orders/:orderId" element={<PurchaseOrderDetailPage />} />
            <Route path="purchases/orders/:orderId/edit" element={<PurchaseOrderEditorPage />} />
            <Route path="purchases/bills/new" element={<BillEditorPage />} />
            <Route path="purchases/bills/:billId" element={<BillDetailPage />} />
            <Route path="purchases/bills/:billId/edit" element={<BillEditorPage />} />
            <Route path="purchases/credits/:creditId" element={<VendorCreditPage />} />
            <Route path="accounting" element={<AccountingPage />} />
            <Route path="projects" element={<ProjectsPage />} />
            <Route path="projects/:projectId" element={<ProjectWorkPage />} />
            <Route path="hr" element={<HrPage />} />
            <Route path="payroll" element={<PayrollPage />} />
            <Route path="bookings" element={<BookingsPage />} />
            <Route path="inventory" element={<InventoryPage />} />
            <Route path="reports" element={<ReportsPage />} />
            <Route path="documents" element={<DocumentsPage />} />
            <Route path="assistant" element={<AssistantPage />} />
            <Route path="assistant/:conversationId" element={<AssistantPage />} />
            <Route path="tasks" element={<TasksPage />} />
            <Route path="notifications" element={<NotificationsPage />} />
            <Route path="approvals" element={<ApprovalsPage />} />
            <Route path="settings/ai" element={<AiSettingsPage />} />
            <Route path="settings/hosting" element={<HostingPage />} />
            <Route path="studio" element={<StudioPage />} />
            <Route path="studio/:projectId" element={<ProjectPage />} />
            <Route path="agents" element={<AgentsPage />} />
            <Route path="agents/:agentId" element={<AgentPage />} />
            <Route path="automation" element={<AutomationPage />} />
            <Route path="automation/new" element={<WorkflowEditorPage />} />
            <Route path="automation/:workflowId" element={<WorkflowPage />} />
            <Route path="automation/:workflowId/edit" element={<WorkflowEditorPage />} />
            <Route path="creative" element={<CreativePage />} />
            <Route path="creative/:kitId" element={<BrandKitPage />} />
            <Route path="settings/members" element={<MembersPage />} />
            <Route path="settings/roles" element={<RolesPage />} />
            <Route path="settings/audit" element={<AuditPage />} />
            <Route path="settings/organization" element={<OrgSettingsPage />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </ErrorBoundary>
  );
}
