import { tokenStore, toApiError, ApiError, UNAUTHORIZED_EVENT, type Schemas } from "../api/client";
import { parseEvents } from "./live";

export type Turn = Schemas["TurnOut"];

/**
 * Send a message and read the reply as it is written (POST .../messages/stream, server-sent events).
 * `onDelta` gets each piece of text, `onTool` each tool the assistant used; resolves with the full turn.
 */
export async function streamTurn(
  orgId: string,
  conversationId: string,
  body: { content: string; deep: boolean },
  handlers: { onDelta: (text: string) => void; onTool?: (name: string, status: string) => void },
): Promise<Turn> {
  const token = tokenStore.get();
  const res = await fetch(`/api/v1/orgs/${orgId}/assistant/conversations/${conversationId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  if (!res.ok || !res.body) throw toApiError(res.status, await res.json().catch(() => undefined));
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const parsed = parseEvents(buffer + value);
    buffer = parsed.rest;
    for (const e of parsed.events) {
      const data = JSON.parse(e.data);
      if (e.event === "delta") handlers.onDelta(data.text);
      else if (e.event === "tool") handlers.onTool?.(data.name, data.status);
      else if (e.event === "error") throw new ApiError(0, data.detail);
      else if (e.event === "done") return data as Turn;
    }
  }
  throw new ApiError(0, "the reply was cut off");
}
