import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, tokenStore, unwrap, UNAUTHORIZED_EVENT, type Schemas } from "../api/client";

type User = Schemas["UserOut"];

type AuthState = {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  // The token is React state (mirrored to storage) so signing in or out always re-renders the app.
  const [token, setToken] = useState<string | null>(tokenStore.get);
  const me = useQuery({
    queryKey: ["me", token],
    queryFn: () => unwrap(api.GET("/api/v1/me")),
    enabled: token !== null,
    retry: false,
    staleTime: 5 * 60_000,
  });

  const signOutLocally = useCallback(() => {
    tokenStore.clear();
    setToken(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    window.addEventListener(UNAUTHORIZED_EVENT, signOutLocally);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, signOutLocally);
  }, [signOutLocally]);

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await unwrap(api.POST("/api/v1/auth/login", { body: { email, password } }));
      tokenStore.set(result.access_token);
      const user = await unwrap(api.GET("/api/v1/me"));
      qc.setQueryData(["me", result.access_token], user);
      setToken(result.access_token);
    },
    [qc],
  );

  const logout = useCallback(async () => {
    try {
      await api.POST("/api/v1/auth/logout"); // revokes the session on the server
    } finally {
      signOutLocally();
    }
  }, [signOutLocally]);

  const value = useMemo<AuthState>(
    () => ({ user: token ? (me.data ?? null) : null, loading: token !== null && me.isPending, login, logout }),
    [token, me.data, me.isPending, login, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
