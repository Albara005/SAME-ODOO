"""The real API for the browser tests, with a deterministic stand-in for the model.

    uvicorn tests.e2e_app:create_app --factory

Never used outside tests: the stand-in answers from fixed rules and calls no external service.
"""

from __future__ import annotations

import json
import time

import httpx

from novara.ai.provider import ModelReply, stream_text, text_reply, tool_reply
from novara.mail import MemoryMailer
from novara.main import create_app as _create_app

ADD_CUSTOMER = "أضف عميل"
REMEMBER = "تذكّر"
LOOK_UP = "ابحث"


class DemoProvider:
    def create(self, *, model: str, effort: str, system: str, tools: list[dict], messages: list[dict],
               max_tokens: int, stream: bool = False, on_text=None) -> ModelReply:
        reply = self._reply(model, tools, messages)
        if on_text is not None:
            stream_text(reply, lambda chunk: (on_text(chunk), time.sleep(0.05)))  # visible in the browser tests
        return reply

    def _reply(self, model: str, tools: list[dict], messages: list[dict]) -> ModelReply:
        last = messages[-1]["content"]
        results = [b for b in last if b.get("type") == "tool_result"]
        if results:
            data = json.loads(results[0]["content"]).get("data") if isinstance(results[0]["content"], str) else None
            if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("text"):  # passages found
                return text_reply(f"وجدت في «{data[0]['filename']}»: {data[0]['text'][:160]}", model=model)
            if isinstance(data, list) and not data:
                return text_reply("لم أجد ذلك في المستندات.", model=model)
            return text_reply("جهّزت الطلب. راجعه ووافق عليه من البطاقة أدناه.", model=model)
        typed = last[-1].get("text", "")  # the last block is what the user typed
        if typed.startswith(REMEMBER) and any(t["name"] == "remember" for t in tools):
            return tool_reply("remember", {"note": typed[len(REMEMBER):].strip(" :،")}, tool_use_id=f"toolu_{len(messages)}",
                              text="سأحفظ هذه الملاحظة بعد موافقتك.", model=model)
        offered = {t["name"] for t in tools}
        if typed.startswith(LOOK_UP) and {"search_knowledge", "search_files"} & offered:
            tool = "search_knowledge" if "search_knowledge" in offered else "search_files"
            return tool_reply(tool, {"query": typed[len(LOOK_UP):].strip(" :،")}, tool_use_id=f"toolu_{len(messages)}",
                              model=model)
        if ADD_CUSTOMER in typed and any(t["name"] == "create_customer" for t in tools):
            name = typed.split(ADD_CUSTOMER, 1)[1].strip(" :،") or "عميل جديد"
            return tool_reply("create_customer", {"name": name}, tool_use_id=f"toolu_{len(messages)}",
                              text="سأضيف العميل بعد موافقتك.", model=model)
        return text_reply(f"وصلتني رسالتك: «{typed}». هذا رد تجريبي للاختبارات.", model=model)


def create_app():
    mailer = MemoryMailer()
    app = _create_app(ai_provider=DemoProvider(), mailer=mailer)

    dns: dict[str, list[str]] = {}
    app.state.domain_lookup = lambda name: dns.get(name, [])  # no real DNS in the browser tests

    @app.post("/api/e2e/dns", include_in_schema=False)
    def set_txt(name: str, value: str) -> dict:
        """The browser tests publish a domain's TXT record here."""
        dns[name] = [value]
        return {"ok": True}

    # ZATCA's Fatoora API: a stand-in that issues certificates and checks each document's hash and signature.
    from tests.fake_zatca import FakeZatca
    app.state.einvoice_http = httpx.Client(transport=httpx.MockTransport(FakeZatca().handler))

    @app.get("/api/e2e/mail", include_in_schema=False)
    def mail_to(to: str) -> list[dict]:
        """The browser tests read the emailed links here (password reset, verification, invitations)."""
        return [{"subject": m.subject, "text": m.text} for m in mailer.sent if m.to == to]

    return app
