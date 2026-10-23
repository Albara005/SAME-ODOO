import { describe, expect, it } from "vitest";
import { parseEvents } from "./live";

describe("parseEvents", () => {
  it("reads complete events, skips heartbeats and keeps the unfinished tail", () => {
    const { events, rest } = parseEvents('event: counts\ndata: {"tasks":1}\n\n: ping\n\nevent: counts\ndata: {"ta');
    expect(events).toEqual([{ event: "counts", data: '{"tasks":1}' }]);
    expect(rest).toBe('event: counts\ndata: {"ta');
    expect(parseEvents(rest + 'sks":2}\n\n').events).toEqual([{ event: "counts", data: '{"tasks":2}' }]);
  });
  it("keeps colons inside data", () => {
    expect(parseEvents('data: {"title":"a: b"}\n\n').events).toEqual([{ event: "message", data: '{"title":"a: b"}' }]);
  });
});
