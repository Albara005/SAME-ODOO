import {
  Bell,
  Bot,
  CalendarClock,
  Boxes,
  CheckSquare,
  ClipboardCheck,
  FileText,
  FolderOpen,
  Home,
  IdCard,
  KeyRound,
  LayoutTemplate,
  ListChecks,
  Package,
  Palette,
  ScrollText,
  Server,
  Settings,
  ShoppingBag,
  ShoppingCart,
  Sparkles,
  Users,
  UsersRound,
  Workflow,
  BarChart3,
  Banknote,
  FolderKanban,
  Landmark,
  type LucideIcon,
} from "lucide-react";
import type { MessageKey } from "../i18n/en";

export type NavItem = { to: string; label: MessageKey; icon: LucideIcon; anyOf?: string[]; hue?: string; desc?: MessageKey };
export type NavGroup = { title?: MessageKey; items: NavItem[] };

/** Menu from the plan (section 13.1). Items show only to users holding one of `anyOf`. */
export const NAV: NavGroup[] = [
  { items: [{ to: "", label: "nav.home", icon: Home }] },
  {
    title: "nav.business",
    items: [
      { to: "customers", label: "nav.customers", icon: UsersRound, anyOf: ["customers.read"], hue: "lapis", desc: "app.customers" },
      { to: "products", label: "nav.products", icon: Package, anyOf: ["products.read"], hue: "teal", desc: "app.products" },
      { to: "sales", label: "nav.sales", icon: ShoppingCart, anyOf: ["sales.read"], hue: "amber", desc: "app.sales" },
      { to: "bookings", label: "nav.bookings", icon: CalendarClock, anyOf: ["bookings.read"], hue: "teal", desc: "app.bookings" },
      { to: "invoices", label: "nav.invoices", icon: FileText, anyOf: ["invoices.read"], hue: "emerald", desc: "app.invoices" },
      { to: "purchases", label: "nav.purchases", icon: ShoppingBag, anyOf: ["purchases.read"], hue: "cyan", desc: "app.purchases" },
      { to: "inventory", label: "nav.inventory", icon: Boxes, anyOf: ["inventory.read"], hue: "terracotta", desc: "app.inventory" },
      { to: "projects", label: "nav.projects", icon: FolderKanban, anyOf: ["projects.read"], hue: "violet", desc: "app.projects" },
      { to: "hr", label: "nav.hr", icon: IdCard, anyOf: ["hr.read", "leave.request", "hr.manage"], hue: "rose", desc: "app.hr" },
      { to: "payroll", label: "nav.payroll", icon: Banknote, anyOf: ["payroll.manage"], hue: "emerald", desc: "app.payroll" },
      { to: "accounting", label: "nav.accounting", icon: Landmark, anyOf: ["accounting.read"], hue: "gold", desc: "app.accounting" },
      { to: "reports", label: "nav.reports", icon: BarChart3, anyOf: ["invoices.read", "accounting.read"], hue: "violet", desc: "app.reports" },
      { to: "documents", label: "nav.documents", icon: FolderOpen, anyOf: ["files.read"], hue: "slate", desc: "app.documents" },
    ],
  },
  {
    title: "nav.ai",
    items: [
      { to: "assistant", label: "nav.assistant", icon: Sparkles, anyOf: ["ai.use"], hue: "gold", desc: "app.assistant" },
      { to: "studio", label: "nav.studio", icon: LayoutTemplate, anyOf: ["apps.read"], hue: "cyan", desc: "app.studio" },
      { to: "agents", label: "nav.agents", icon: Bot, anyOf: ["agents.run"], hue: "slate", desc: "app.agents" },
      { to: "creative", label: "nav.creative", icon: Palette, anyOf: ["brand.read"], hue: "rose", desc: "app.creative" },
      { to: "automation", label: "nav.automation", icon: Workflow, anyOf: ["workflows.manage"], hue: "lapis", desc: "app.automation" },
    ],
  },
  {
    title: "nav.work",
    items: [
      { to: "tasks", label: "nav.tasks", icon: ListChecks },
      { to: "approvals", label: "nav.approvals", icon: ClipboardCheck },
      { to: "notifications", label: "nav.notifications", icon: Bell },
    ],
  },
  {
    title: "nav.admin",
    items: [
      { to: "settings/members", label: "nav.members", icon: Users, anyOf: ["members.read"] },
      { to: "settings/roles", label: "nav.roles", icon: KeyRound, anyOf: ["members.read"] },
      { to: "settings/audit", label: "nav.audit", icon: ScrollText, anyOf: ["audit.read"] },
      { to: "settings/organization", label: "nav.organization", icon: Settings, anyOf: ["org.manage"] },
      { to: "settings/ai", label: "nav.aiSettings", icon: CheckSquare, anyOf: ["ai.manage"] },
      { to: "settings/hosting", label: "nav.hosting", icon: Server, anyOf: ["hosting.manage"] },
    ],
  },
];

export function visibleNav(can: (p: string) => boolean): NavGroup[] {
  return NAV.map((g) => ({ ...g, items: g.items.filter((i) => !i.anyOf || i.anyOf.some(can)) })).filter(
    (g) => g.items.length > 0,
  );
}
