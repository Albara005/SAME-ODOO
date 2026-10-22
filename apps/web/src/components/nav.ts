import {
  Bell,
  Bot,
  Boxes,
  CheckSquare,
  ClipboardCheck,
  FileText,
  Home,
  KeyRound,
  LayoutTemplate,
  ListChecks,
  Package,
  Palette,
  ScrollText,
  Server,
  Settings,
  ShoppingCart,
  Sparkles,
  Users,
  UsersRound,
  Workflow,
  BarChart3,
  type LucideIcon,
} from "lucide-react";
import type { MessageKey } from "../i18n/en";

export type NavItem = { to: string; label: MessageKey; icon: LucideIcon; anyOf?: string[] };
export type NavGroup = { title?: MessageKey; items: NavItem[] };

/** Menu from the plan (section 13.1). Items show only to users holding one of `anyOf`. */
export const NAV: NavGroup[] = [
  { items: [{ to: "", label: "nav.home", icon: Home }] },
  {
    title: "nav.business",
    items: [
      { to: "customers", label: "nav.customers", icon: UsersRound, anyOf: ["customers.read"] },
      { to: "products", label: "nav.products", icon: Package, anyOf: ["products.read"] },
      { to: "sales", label: "nav.sales", icon: ShoppingCart, anyOf: ["sales.read"] },
      { to: "invoices", label: "nav.invoices", icon: FileText, anyOf: ["invoices.read"] },
      { to: "inventory", label: "nav.inventory", icon: Boxes, anyOf: ["inventory.read"] },
      { to: "reports", label: "nav.reports", icon: BarChart3, anyOf: ["invoices.read", "accounting.read"] },
    ],
  },
  {
    title: "nav.ai",
    items: [
      { to: "assistant", label: "nav.assistant", icon: Sparkles, anyOf: ["ai.use"] },
      { to: "studio", label: "nav.studio", icon: LayoutTemplate, anyOf: ["apps.read"] },
      { to: "agents", label: "nav.agents", icon: Bot, anyOf: ["agents.run"] },
      { to: "creative", label: "nav.creative", icon: Palette, anyOf: ["brand.read"] },
      { to: "automation", label: "nav.automation", icon: Workflow, anyOf: ["workflows.manage"] },
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
