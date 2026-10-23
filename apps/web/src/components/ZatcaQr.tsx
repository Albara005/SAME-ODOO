import { useEffect, useState } from "react";
import { useI18n } from "../i18n/I18n";

/** The e-invoice QR code (ZATCA phase 1). The payload is the server's base64 TLV, shown as-is. */
export function ZatcaQr({ payload }: { payload: string }) {
  const { t } = useI18n();
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    void import("qrcode")
      .then((QRCode) => QRCode.toString(payload, { type: "svg", margin: 1, errorCorrectionLevel: "M" }))
      .then((svg) => current && setSrc(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`));
    return () => {
      current = false;
    };
  }, [payload]);
  return (
    <figure className="zatca-qr">
      {src ? <img src={src} alt={t("zatca.alt")} width={132} height={132} /> : <span className="zatca-qr-placeholder" />}
      <figcaption className="small muted">{t("zatca.caption")}</figcaption>
    </figure>
  );
}
