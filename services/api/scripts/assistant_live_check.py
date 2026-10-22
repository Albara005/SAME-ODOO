"""Ask the real assistant one question against a throwaway local database.

This calls the Claude API and costs real money (a few cents). It needs credentials in
the environment (ANTHROPIC_API_KEY, or an `ant auth login` profile).

    cd services/api && python -m scripts.assistant_live_check "Which invoices are overdue?"
"""

from __future__ import annotations

import sys
import tempfile

from fastapi.testclient import TestClient

from novara.config import Settings
from novara.main import create_app


def main(question: str) -> None:
    tmp = tempfile.mkdtemp()
    app = create_app(Settings(env="development", database_url=f"sqlite:///{tmp}/live.db",
                              jwt_secret="live-check-secret-that-is-long-enough-123", storage_root=f"{tmp}/s"))
    c = TestClient(app)
    c.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": "live-check-password"})
    token = c.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "live-check-password"}).json()
    h = {"Authorization": f"Bearer {token['access_token']}"}
    org = c.post("/api/v1/orgs", json={"name": "Demo Co", "slug": "demo-co"}, headers=h).json()["id"]
    base = f"/api/v1/orgs/{org}"
    cust = c.post(f"{base}/customers", json={"name": "شركة النخبة"}, headers=h).json()["id"]
    inv = c.post(f"{base}/invoices", headers=h, json={"customer_id": cust, "issue_date": "2026-01-05",
                 "due_date": "2026-02-04", "lines": [{"description": "Consulting", "quantity": "4", "unit_price": "750"}]}).json()
    c.post(f"{base}/invoices/{inv['id']}/post", headers=h)
    conv = c.post(f"{base}/assistant/conversations", json={}, headers=h).json()["id"]
    out = c.post(f"{base}/assistant/conversations/{conv}/messages", json={"content": question}, headers=h).json()
    print("reply:", out["reply"])
    print("tools:", [(t["name"], t["status"]) for t in out["tool_calls"]])
    print("stop:", out["stop"], "| cost USD:", out["cost_usd"])


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]) or "Which invoices are overdue, and how much is owed?")
