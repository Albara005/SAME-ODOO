import { QueryClient, QueryObserver, type QueryKey } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { refreshUser } from "./refreshUser";

/** The server's answer to "who am I", which changes while the first read is still on its way. */
function server() {
  let verified = false;
  const pending: (() => void)[] = [];
  return {
    verify: () => { verified = true; },
    // Reads the user now, answers when released: like a request the server handled before the change.
    read: () => {
      const snapshot = { email_verified: verified };
      return new Promise<typeof snapshot>((resolve) => pending.push(() => resolve(snapshot)));
    },
    releaseAll: () => pending.splice(0).forEach((release) => release()),
  };
}

type Me = { email_verified: boolean };

async function settle(observer: QueryObserver<Me, Error, Me, Me, QueryKey>, srv: ReturnType<typeof server>) {
  for (let i = 0; i < 5; i++) {
    srv.releaseAll();
    await new Promise((r) => setTimeout(r, 0));
  }
  return observer.getCurrentResult().data;
}

describe("refreshUser", () => {
  it("does not keep a read that started before the change", async () => {
    const srv = server();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5 * 60_000 } } });
    const observer = new QueryObserver<Me, Error, Me, Me, QueryKey>(qc, { queryKey: ["me", "token"], queryFn: srv.read });
    const unsubscribe = observer.subscribe(() => undefined);   // the first read is now in flight
    srv.verify();                                                // the email is confirmed meanwhile
    const refreshed = refreshUser(qc);
    expect(await settle(observer, srv)).toEqual({ email_verified: true });
    await refreshed;
    unsubscribe();
  });

  it("a plain invalidate would have kept the stale read (why refreshUser exists)", async () => {
    const srv = server();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5 * 60_000 } } });
    const observer = new QueryObserver<Me, Error, Me, Me, QueryKey>(qc, { queryKey: ["me", "token"], queryFn: srv.read });
    const unsubscribe = observer.subscribe(() => undefined);
    srv.verify();
    const invalidated = qc.invalidateQueries({ queryKey: ["me"] });
    expect(await settle(observer, srv)).toEqual({ email_verified: false });
    await invalidated;
    unsubscribe();
  });
});
