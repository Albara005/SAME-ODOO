import { useQueryClient } from "@tanstack/react-query";
import { Fingerprint } from "lucide-react";
import { useState } from "react";
import { Button, ErrorNotice, Notice } from "../components/ui";
import { useI18n } from "../i18n/I18n";
import { passkeysSupported } from "../lib/webauthn";
import { useAuth } from "./Auth";

/** Sign in again with a passkey, replacing a password-only session with a two-step one. */
export function PasskeySignInButton({ primary = false, size }: { primary?: boolean; size?: "sm" }) {
  const { t } = useI18n();
  const { loginWithPasskey } = useAuth();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!passkeysSupported()) return null;
  async function run() {
    setBusy(true);
    setError(null);
    try {
      await loginWithPasskey();
      void qc.invalidateQueries({ queryKey: ["sessions"] });
    } catch (err) {
      // The browser says NotAllowedError when the person cancels or no passkey for this site is found.
      setError(err instanceof DOMException ? new Notice(t("passkey.notUsed")) : err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {error !== null && <ErrorNotice error={error} />}
      <Button variant={primary ? "primary" : undefined} size={size} icon={<Fingerprint size={size ? 14 : 16} />}
        loading={busy} onClick={() => void run()}>
        {t("passkey.signIn")}
      </Button>
    </>
  );
}
