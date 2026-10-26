"""The e-invoicing certificate's expiry: warned about ahead (in the settings, on the invoice screens and in reminders to
the organization's managers), posting is refused once it has expired, and onboarding again renews it while the
device's chain of documents carries on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from novara.einvoice import service as einvoice
from novara.einvoice.models import EInvoiceSettings
from novara.tenancy import bind_tenant
from novara.worker import remind_certificates
from tests.test_einvoicing import ACCT, OWNER, SALES, ei, onboard, post_invoice, stored, u, zatca  # noqa: F401 (fixtures)


def expires_in(app, s, delta: timedelta) -> None:
    with app.state.sessionmaker() as db:
        bind_tenant(db, s["org"])
        db.scalar(select(EInvoiceSettings)).certificate_expires_at = datetime.now(timezone.utc) + delta
        db.commit()


def reminders(api, user=OWNER, org=None):
    return [n for n in api.req("GET", user, f"/orgs/{org}/notifications").json() if "certificate" in n["title"].lower()
            or "شهادة" in n["title"]]


def test_the_certificate_is_warned_about_then_renewed_and_the_chain_carries_on(api, ei, zatca, app):
    done = onboard(api, ei)
    assert (done["certificate_state"], done["certificate_days_left"] > 300) == ("ok", True)
    first = post_invoice(api, ei, ei["cash"]).json()
    assert remind_certificates(app.state.sessionmaker) == {}                     # nothing yet

    expires_in(app, ei, timedelta(days=10, hours=1))
    settings = api.req("GET", OWNER, u(ei, "/einvoicing")).json()
    assert (settings["certificate_state"], settings["certificate_days_left"]) == ("expiring", 10)
    assert api.req("GET", SALES, u(ei, "/einvoicing/status")).json()["certificate_state"] == "expiring"
    assert remind_certificates(app.state.sessionmaker) == {ei["org"]: 1}         # the owner (org.manage)
    assert remind_certificates(app.state.sessionmaker) == {}                     # once per step
    assert len(reminders(api, org=ei["org"])) == 1 and reminders(api, ACCT, ei["org"]) == []
    expires_in(app, ei, timedelta(days=2, hours=1))
    assert remind_certificates(app.state.sessionmaker) == {ei["org"]: 1}         # the 3-day reminder

    # Expired: nothing can be signed with it, so posting waits for the renewal.
    expires_in(app, ei, timedelta(hours=-1))
    assert api.req("GET", OWNER, u(ei, "/einvoicing")).json()["certificate_state"] == "expired"
    refused = post_invoice(api, ei, ei["cash"])
    assert refused.status_code == 409 and "expired" in refused.json()["detail"]
    assert remind_certificates(app.state.sessionmaker) == {ei["org"]: 1}
    assert len(reminders(api, org=ei["org"])) == 3

    renewed = api.req("POST", OWNER, u(ei, "/einvoicing/onboard"), json={"otp": "123345"})
    assert renewed.status_code == 200 and renewed.json()["certificate_state"] == "ok"
    second = post_invoice(api, ei, ei["cash"]).json()
    assert second["einvoice"]["icv"] == 2                                         # the same device's next document
    one, two = stored(app, ei, first["number"]), stored(app, ei, second["number"])
    assert one.invoice_hash in two.xml                                            # its PIH is the first one's hash
    actions = [a["action"] for a in api.req("GET", OWNER, f"/orgs/{ei['org']}/audit-logs").json()]
    assert "einvoicing.renewed" in actions and "einvoicing.onboarded" in actions


def test_turning_off_and_onboarding_again_starts_a_new_device(api, ei, zatca, app):
    onboard(api, ei)
    post_invoice(api, ei, ei["cash"])
    assert api.req("POST", OWNER, u(ei, "/einvoicing/off")).status_code == 200
    assert api.req("GET", SALES, u(ei, "/einvoicing/status")).json()["certificate_state"] is None
    again = api.req("POST", OWNER, u(ei, "/einvoicing/onboard"), json={"otp": "123345"})
    assert again.status_code == 200
    assert post_invoice(api, ei, ei["cash"]).json()["einvoice"]["icv"] == 1


def test_the_state_by_the_clock():
    s = EInvoiceSettings(status="onboarded", certificate_expires_at=datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc))
    at = lambda iso: datetime.fromisoformat(iso)  # noqa: E731
    assert (einvoice.certificate_state(s, at("2026-05-31T12:00:00+00:00")),
            einvoice.certificate_days_left(s, at("2026-05-31T12:00:00+00:00"))) == ("ok", 30)    # exactly 30 days
    assert einvoice.certificate_state(s, at("2026-05-31T13:00:00+00:00")) == "expiring"    # 29 days and 23 hours
    assert einvoice.certificate_state(s, at("2026-06-29T12:00:00+00:00")) == "expiring"
    assert einvoice.certificate_days_left(s, at("2026-06-30T11:00:00+00:00")) == 0
    assert einvoice.certificate_state(s, at("2026-06-30T12:00:00+00:00")) == "expired"
    assert einvoice.certificate_days_left(s, at("2026-07-01T12:00:00+00:00")) == -1
    assert einvoice.certificate_state(EInvoiceSettings(status="off", certificate_expires_at=None)) is None
