import { describe, expect, it } from "vitest";
import { creationOptions, fromBase64url, requestOptions, toBase64url } from "./webauthn";

describe("passkey options", () => {
  it("round-trips base64url without padding, including bytes that need - and _", () => {
    const bytes = new Uint8Array([0, 251, 255, 190, 63, 1]);
    const text = toBase64url(bytes.buffer);
    expect(text).toBe("APv_vj8B");
    expect([...new Uint8Array(fromBase64url(text))]).toEqual([...bytes]);
    expect([...new Uint8Array(fromBase64url("AQ"))]).toEqual([1]);
  });

  it("turns the server's JSON into what navigator.credentials expects", () => {
    const created = creationOptions({
      challenge: "AQID", rp: { id: "localhost", name: "NOVARA" }, user: { id: "dXNlcg", name: "a@b.c", displayName: "A" },
      pubKeyCredParams: [{ type: "public-key", alg: -7 }], excludeCredentials: [{ id: "BAU", type: "public-key" }],
    });
    expect([...new Uint8Array(created.challenge as ArrayBuffer)]).toEqual([1, 2, 3]);
    expect(new TextDecoder().decode(created.user.id as ArrayBuffer)).toBe("user");
    expect([...new Uint8Array(created.excludeCredentials![0]!.id as ArrayBuffer)]).toEqual([4, 5]);
    expect(created.rp.id).toBe("localhost");

    const asked = requestOptions({ challenge: "AQID", rpId: "localhost", userVerification: "required" });
    expect(asked.allowCredentials).toEqual([]);
    expect(asked.userVerification).toBe("required");
  });
});
