"""The real API for the browser tests, with a deterministic stand-in for the model.

    uvicorn tests.e2e_app:create_app --factory

Never used outside tests: the stand-in answers from fixed rules and calls no external service.
"""

from __future__ import annotations

from novara.ai.provider import ModelReply, text_reply, tool_reply
from novara.mail import MemoryMailer
from novara.main import create_app as _create_app

ADD_CUSTOMER = "أضف عميل"


class DemoProvider:
    def create(self, *, model: str, effort: str, system: str, tools: list[dict], messages: list[dict],
               max_tokens: int, stream: bool = False) -> ModelReply:
        last = messages[-1]["content"]
        if any(b.get("type") == "tool_result" for b in last):
            return text_reply("جهّزت الطلب. راجعه ووافق عليه من البطاقة أدناه.", model=model)
        typed = last[-1].get("text", "")  # the last block is what the user typed
        if ADD_CUSTOMER in typed and any(t["name"] == "create_customer" for t in tools):
            name = typed.split(ADD_CUSTOMER, 1)[1].strip(" :،") or "عميل جديد"
            return tool_reply("create_customer", {"name": name}, tool_use_id=f"toolu_{len(messages)}",
                              text="سأضيف العميل بعد موافقتك.", model=model)
        return text_reply(f"وصلتني رسالتك: «{typed}». هذا رد تجريبي للاختبارات.", model=model)


def create_app():
    mailer = MemoryMailer()
    app = _create_app(ai_provider=DemoProvider(), mailer=mailer)

    @app.get("/api/e2e/mail", include_in_schema=False)
    def mail_to(to: str) -> list[dict]:
        """The browser tests read the emailed links here (password reset, verification, invitations)."""
        return [{"subject": m.subject, "text": m.text} for m in mailer.sent if m.to == to]

    return app
