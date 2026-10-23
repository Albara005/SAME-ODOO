import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, tokenStore, unwrap, UNAUTHORIZED_EVENT, type Schemas } from "../api/client";

type User = Schemas["UserOut"];

type AuthState = {
  user: User | null;
  loading: boolean;
  /** Signs in, or returns the token for the second step when the account uses two-factor sign-in. */
  login: (email: string, password: string) => Promise<{ mfaToken: string } | null>;
  completeMfa: (mfaToken: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
  /** True after the user signed out themselves (not when the session expired). */
  signedOut: boolean;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  // The token is React state (mirrored to storage) so signing in or out always re-renders the app.
  const [token, setToken] = useState<string | null>(tokenStore.get);
  const [signedOut, setSignedOut] = useState(false);
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

  const startSession = useCallback(
    async (accessToken: string) => {
      tokenStore.set(accessToken);
      const user = await unwrap(api.GET("/api/v1/me"));
      qc.setQueryData(["me", accessToken], user);
      setToken(accessToken);
      setSignedOut(false);
    },
    [qc],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await unwrap(api.POST("/api/v1/auth/login", { body: { email, password } }));
      if (result.mfa_required && result.mfa_token) return { mfaToken: result.mfa_token };
      await startSession(result.access_token!);
      return null;
    },
    [startSession],
  );

  const completeMfa = useCallback(
    async (mfaToken: string, code: string) => {
      const result = await unwrap(api.POST("/api/v1/auth/login/mfa", { body: { mfa_token: mfaToken, code } }));
      await startSession(result.access_token);
    },
    [startSession],
  );

  const logout = useCallback(async () => {
    try {
      await api.POST("/api/v1/auth/logout"); // revokes the session on the server
    } finally {
      setSignedOut(true);
      signOutLocally();
    }
  }, [signOutLocally]);

  const value = useMemo<AuthState>(
    () => ({ user: token ? (me.data ?? null) : null, loading: token !== null && me.isPending, login, completeMfa, logout, signedOut }),
    [token, me.data, me.isPending, login, completeMfa, logout, signedOut],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
