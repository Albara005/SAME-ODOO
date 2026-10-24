/**
 * Passkeys in the browser: the server sends WebAuthn options as JSON with base64url strings for
 * binary fields; navigator.credentials wants ArrayBuffers, and the server wants JSON back.
 * Done by hand rather than with PublicKeyCredential.parse*FromJSON so older browsers work too.
 */

type Json = Record<string, unknown>;

export function fromBase64url(value: string): ArrayBuffer {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

export function toBase64url(buffer: ArrayBuffer | ArrayBufferView): string {
  const bytes = buffer instanceof ArrayBuffer ? new Uint8Array(buffer) : new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function passkeysSupported(): boolean {
  return typeof window !== "undefined" && typeof window.PublicKeyCredential === "function" && Boolean(navigator.credentials);
}

type Descriptor = { id: string; type: "public-key"; transports?: AuthenticatorTransport[] };
const descriptors = (list: unknown) =>
  ((list as Descriptor[] | undefined) ?? []).map((d) => ({ ...d, id: fromBase64url(d.id) }));

export function creationOptions(json: Json): PublicKeyCredentialCreationOptions {
  const user = json.user as { id: string; name: string; displayName: string };
  return {
    ...(json as unknown as PublicKeyCredentialCreationOptions),
    challenge: fromBase64url(json.challenge as string),
    user: { ...user, id: fromBase64url(user.id) },
    excludeCredentials: descriptors(json.excludeCredentials),
  };
}

export function requestOptions(json: Json): PublicKeyCredentialRequestOptions {
  return {
    ...(json as unknown as PublicKeyCredentialRequestOptions),
    challenge: fromBase64url(json.challenge as string),
    allowCredentials: descriptors(json.allowCredentials),
  };
}

/** The credential as the server expects it (both registration and sign-in). */
export function credentialJson(credential: PublicKeyCredential): Json {
  const r = credential.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse;
  const response: Json = { clientDataJSON: toBase64url(r.clientDataJSON) };
  if ("attestationObject" in r && r.attestationObject) {
    response.attestationObject = toBase64url(r.attestationObject);
    response.transports = typeof r.getTransports === "function" ? r.getTransports() : [];
  } else {
    response.authenticatorData = toBase64url(r.authenticatorData);
    response.signature = toBase64url(r.signature);
    response.userHandle = r.userHandle ? toBase64url(r.userHandle) : null;
  }
  return {
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    response,
    clientExtensionResults: credential.getClientExtensionResults(),
    authenticatorAttachment: credential.authenticatorAttachment ?? null,
  };
}
