import { Component, type ReactNode } from "react";
import { Navigate, Outlet, Route, Routes, useLocation, useParams } from "react-router-dom";
import { useAuth } from "./auth/Auth";
import { lastOrg, OrgProvider, rememberOrg, useOrgQuery } from "./auth/Org";
import { Layout } from "./components/Layout";
import { ErrorNotice, Loading } from "./components/ui";
import { useI18n } from "./i18n/I18n";
import { AuditPage, MembersPage, OrgSettingsPage, RolesPage } from "./pages/Admin";
import { LoginPage, RegisterPage } from "./pages/Auth";
import { CustomersPage } from "./pages/Customers";
import { HomePage } from "./pages/Home";
import { InvoiceDetailPage, InvoiceEditorPage, InvoicesPage } from "./pages/Invoices";
import { InventoryPage } from "./pages/Inventory";
import { OrgsPage, useMyOrgs } from "./pages/Orgs";
import { ProductsPage } from "./pages/Products";
import { ReportsPage } from "./pages/Reports";
import { SalesOrderDetailPage, SalesOrderEditorPage, SalesOrdersPage } from "./pages/Sales";
import { SoonPage } from "./pages/Soon";

class ErrorBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function RequireAuth() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <Outlet />;
}

function GuestOnly() {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  return user ? <Navigate to="/" replace /> : <Outlet />;
}

function RootRedirect() {
  const orgs = useMyOrgs();
  if (orgs.isPending) return <Loading />;
  const list = orgs.data ?? [];
  const saved = lastOrg();
  const target = list.find((o) => o.id === saved) ?? (list.length === 1 ? list[0] : undefined);
  return <Navigate to={target ? `/o/${target.id}` : "/orgs"} replace />;
}

function OrgRoute() {
  const { orgId = "" } = useParams();
  const org = useOrgQuery(orgId);
  if (org.isPending) return <Loading />;
  if (org.isError) return <Navigate to="/orgs" replace />;
  rememberOrg(org.data.id);
  return (
    <OrgProvider org={org.data}>
      <Layout />
    </OrgProvider>
  );
}

function NotFound() {
  const { t } = useI18n();
  return <ErrorNotice error={new Error(t("error.notFound"))} />;
}

export function App() {
  const { t } = useI18n();
  return (
    <ErrorBoundary fallback={<div className="content"><ErrorNotice error={new Error(t("error.boundary"))} /></div>}>
      <Routes>
        <Route element={<GuestOnly />}>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
        </Route>
        <Route element={<RequireAuth />}>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/orgs" element={<OrgsPage />} />
          <Route path="/o/:orgId" element={<OrgRoute />}>
            <Route index element={<HomePage />} />
            <Route path="customers" element={<CustomersPage />} />
            <Route path="products" element={<ProductsPage />} />
            <Route path="invoices" element={<InvoicesPage />} />
            <Route path="invoices/new" element={<InvoiceEditorPage />} />
            <Route path="invoices/:invoiceId" element={<InvoiceDetailPage />} />
            <Route path="invoices/:invoiceId/edit" element={<InvoiceEditorPage />} />
            <Route path="sales" element={<SalesOrdersPage />} />
            <Route path="sales/new" element={<SalesOrderEditorPage />} />
            <Route path="sales/:orderId" element={<SalesOrderDetailPage />} />
            <Route path="sales/:orderId/edit" element={<SalesOrderEditorPage />} />
            <Route path="inventory" element={<InventoryPage />} />
            <Route path="reports" element={<ReportsPage />} />
            <Route path="settings/members" element={<MembersPage />} />
            <Route path="settings/roles" element={<RolesPage />} />
            <Route path="settings/audit" element={<AuditPage />} />
            <Route path="settings/organization" element={<OrgSettingsPage />} />
            <Route path="*" element={<SoonPage />} />
          </Route>
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </ErrorBoundary>
  );
}
