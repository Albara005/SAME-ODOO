import type { QueryClient } from "@tanstack/react-query";

/**
 * Read the signed-in user again after something changed it (email confirmed, two-step sign-in on or off).
 *
 * A plain invalidate is not enough right after a page load: while the first read of the user is still
 * in flight, React Query joins that read instead of starting another, and that read may have reached
 * the server before the change. So the read in flight is cancelled first, and a fresh one starts.
 */
export async function refreshUser(qc: QueryClient) {
  await qc.cancelQueries({ queryKey: ["me"] });
  await qc.invalidateQueries({ queryKey: ["me"] });
}
