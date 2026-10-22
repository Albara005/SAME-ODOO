import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import { api, unwrap, type Schemas } from "../api/client";

export type Org = Schemas["MyOrgOut"];

type OrgState = {
  org: Org;
  /** Server-side checks are what protect data; this only hides what the user can't use. */
  can: (permission: string) => boolean;
  path: (rest?: string) => string;
};

const Ctx = createContext<OrgState | null>(null);
const LAST_ORG = "novara.org";

export function rememberOrg(id: string) {
  try {
    localStorage.setItem(LAST_ORG, id);
  } catch {
    /* ignore */
  }
}

export function lastOrg(): string | null {
  try {
    return localStorage.getItem(LAST_ORG);
  } catch {
    return null;
  }
}

export function useOrgQuery(orgId: string) {
  return useQuery({
    queryKey: ["org", orgId],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}", { params: { path: { org_id: orgId } } })),
    retry: false,
  });
}

export function OrgProvider({ org, children }: { org: Org; children: ReactNode }) {
  const perms = new Set(org.permissions);
  const value: OrgState = {
    org,
    can: (p) => perms.has(p),
    path: (rest = "") => `/o/${org.id}${rest ? `/${rest.replace(/^\//, "")}` : ""}`,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useOrg(): OrgState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useOrg outside OrgProvider");
  return ctx;
}
