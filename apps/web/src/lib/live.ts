import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { tokenStore, UNAUTHORIZED_EVENT } from "../api/client";

export type LiveCounts = {
  notifications: number;
  approvals: number;
  tasks: number;
  latest_notification: { id: string; title: string } | null;
};

/** Split a server-sent-events buffer into complete events; returns them and the unfinished rest. */
export function parseEvents(buffer: string): { events: { event: string; data: string }[]; rest: string } {
  const events: { event: string; data: string }[] = [];
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue; // comment / heartbeat
      const [field, ...value] = line.split(":");
      const v = value.join(":").replace(/^ /, "");
      if (field === "event") event = v;
      else if (field === "data") data.push(v);
    }
    if (data.length) events.push({ event, data: data.join("\n") });
  }
  return { events, rest };
}

/**
 * The signed-in user's counts, pushed by the server as they change (GET /orgs/{id}/live).
 * Reconnects with a short backoff; refreshes the related lists and calls `onNotification`
 * for each new unread notification after the first event.
 */
export function useLiveCounts(orgId: string, onNotification: (title: string) => void): LiveCounts | null {
  const qc = useQueryClient();
  const [counts, setCounts] = useState<LiveCounts | null>(null);
  const notify = useRef(onNotification);
  notify.current = onNotification;

  useEffect(() => {
    let stopped = false;
    let controller: AbortController | null = null;
    let previous: LiveCounts | null = null;
    let failures = 0;

    function apply(next: LiveCounts) {
      if (previous) {
        if (next.notifications !== previous.notifications) void qc.invalidateQueries({ queryKey: ["notifications", orgId] });
        if (next.approvals !== previous.approvals) void qc.invalidateQueries({ queryKey: ["approvals", orgId] });
        if (next.tasks !== previous.tasks) void qc.invalidateQueries({ queryKey: ["tasks", orgId] });
        const latest = next.latest_notification;
        if (latest && latest.id !== previous.latest_notification?.id && next.notifications > previous.notifications) {
          notify.current(latest.title);
        }
      }
      previous = next;
      setCounts(next);
    }

    async function connect() {
      while (!stopped) {
        controller = new AbortController();
        try {
          const token = tokenStore.get();
          const res = await fetch(`/api/v1/orgs/${orgId}/live`, {
            headers: { Accept: "text/event-stream", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
            signal: controller.signal,
          });
          if (res.status === 401) {
            tokenStore.clear();
            window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
            return;
          }
          if (!res.ok || !res.body) throw new Error(`live: ${res.status}`);
          failures = 0;
          const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
          let buffer = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            const parsed = parseEvents(buffer + value);
            buffer = parsed.rest;
            for (const e of parsed.events) {
              if (e.event === "counts") apply(JSON.parse(e.data) as LiveCounts);
              if (e.event === "end") return; // no longer a member: stop instead of retrying
            }
          }
        } catch {
          if (stopped) return;
          failures += 1;
        }
        // The server closes streams every few minutes; reconnect at once, back off only after errors.
        const wait = failures === 0 ? 250 : Math.min(30_000, 1000 * 2 ** failures);
        await new Promise((r) => setTimeout(r, wait));
      }
    }

    void connect();
    return () => {
      stopped = true;
      controller?.abort();
    };
  }, [orgId, qc]);

  return counts;
}
