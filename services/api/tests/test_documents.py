"""Documents: text read out of PDF, Word and text files, ranked passages for the assistant, and agent knowledge."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from novara.ai.provider import ScriptedProvider, text_reply, tool_reply
from novara.modules.files import text as T
from novara.modules.files.models import FileChunk, StoredFile
from novara.tenancy import bind_tenant

OWNER, SALES, OTHER, CURATOR = (f"{n}@docs.example.com" for n in ("owner", "sales", "other", "curator"))
PDF_FIXTURE = Path(__file__).parent / "fixtures" / "refund-policy-ar.pdf"  # made by Chromium: shaped Arabic glyphs
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def docx(*paragraphs: str) -> bytes:
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document {w}><w:body>{body}</w:body></w:document>")
    return buf.getvalue()


@pytest.fixture
def docs(api, app, monkeypatch):
    monkeypatch.setattr(app.state, "settings", replace(app.state.settings, max_upload_bytes=1_000_000))  # the PDF is 40 KB
    for e in (OWNER, SALES, OTHER, CURATOR):
        api.signup(e)
    org = api.create_org(OWNER, "docs-co")
    api.add_member(OWNER, org, SALES, "sales")
    app.state.ai_provider = ScriptedProvider()
    return {"org": org, "ai": app.state.ai_provider, "app": app}


def upload(api, s, filename, data, content_type, user=OWNER):
    r = api.req("POST", user, f"/orgs/{s['org']}/files", params={"filename": filename}, content=data,
                headers={"Content-Type": content_type})
    assert r.status_code == 201, r.text
    return r.json()


def search(api, s, q, user=OWNER):
    r = api.req("GET", user, f"/orgs/{s['org']}/files/search", params={"q": q})
    assert r.status_code == 200, r.text
    return r.json()


def test_text_is_read_from_pdf_word_and_text_files(api, docs):
    pdf = upload(api, docs, "سياسة الاسترجاع.pdf", PDF_FIXTURE.read_bytes(), "application/pdf")
    assert pdf["text_status"] == "ready" and pdf["text_chars"] > 100
    word = upload(api, docs, "دليل الموظف.docx", docx("الدوام من الأحد إلى الخميس.", "تُصرف الفاتوره الشهرية في اليوم الأول."),
                  DOCX)
    assert word["text_status"] == "ready"
    upload(api, docs, "prices.csv", "product,price\nArabic coffee,40\n".encode(), "text/csv")
    assert upload(api, docs, "logo.png", b"\x89PNG\r\n\x1a\n...", "image/png")["text_status"] == "none"
    assert upload(api, docs, "broken.pdf", b"%PDF-1.4 not really", "application/pdf")["text_status"] == "none"

    # Hamza, diacritics, the article and ta marbuta don't stop a match.
    found = search(api, docs, "إرجاع المنتج")
    assert found[0]["filename"] == "سياسة الاسترجاع.pdf"
    assert "إرجاع المنتج خلال أربعة عشر يوماً" in found[0]["text"]     # back in reading order, not visual order
    assert search(api, docs, "الفَاتُورَة الشهرية")[0]["filename"] == "دليل الموظف.docx"
    assert search(api, docs, "دوام")[0]["filename"] == "دليل الموظف.docx"
    assert search(api, docs, "refunds")[0]["filename"] == "سياسة الاسترجاع.pdf"   # English, plural
    assert search(api, docs, "coffee")[0]["filename"] == "prices.csv"
    assert search(api, docs, "في من على") == []                                   # nothing but small words
    assert search(api, docs, "مرور") == []

    # Other organizations see nothing; deleting a file deletes its passages.
    other = api.create_org(OTHER, "other-docs")
    assert api.req("GET", OTHER, f"/orgs/{other}/files/search", params={"q": "إرجاع"}).json() == []
    assert api.req("DELETE", OWNER, f"/orgs/{docs['org']}/files/{word['id']}").status_code == 204
    assert search(api, docs, "الدوام") == []


def test_files_from_before_are_read_on_the_next_search(api, docs, monkeypatch):
    f = upload(api, docs, "terms.txt", "Payment terms: net 30 days from the invoice date.".encode(), "text/plain")
    with docs["app"].state.sessionmaker() as db:
        bind_tenant(db, docs["org"])
        db.query(FileChunk).delete()
        db.get(StoredFile, f["id"]).text_status = "pending"
        db.commit()
    assert search(api, docs, "payment terms")[0]["file_id"] == f["id"]

    monkeypatch.setattr(T, "MAX_DOCX_XML_BYTES", 10)    # a Word file that unpacks to too much isn't read
    assert upload(api, docs, "big.docx", docx("x" * 100), DOCX)["text_status"] == "none"


def test_passages_are_cut_between_paragraphs_and_ranked(api, docs):
    para = "سياسة الشحن: يصل الطلب خلال ثلاثة أيام عمل داخل الرياض. "
    long = "\n\n".join([para * 3] + ["فقرة عامة عن الشركة وتاريخها. " * 4] * 12 + ["الشحن مجاني للطلبات فوق مئتي ريال."]
                         + ["كلمة " * 400])                            # one paragraph longer than a passage
    pieces = T.chunks(long)
    assert len(pieces) >= 4 and all(len(p) <= T.CHUNK_CHARS for p in pieces)
    assert pieces[0].startswith("سياسة الشحن") and not any(p.startswith(" ") for p in pieces)
    upload(api, docs, "shipping.txt", long.encode(), "text/plain")
    upload(api, docs, "about.txt", ("الشحن مذكور مرة واحدة هنا. " + "نبذة عن الشركة. " * 40).encode(), "text/plain")
    top = search(api, docs, "الشحن خلال أيام")
    assert top[0]["filename"] == "shipping.txt" and top[0]["score"] >= top[-1]["score"]


def test_the_assistant_finds_passages_and_reads_whole_documents(api, docs):
    pdf = upload(api, docs, "policy.pdf", PDF_FIXTURE.read_bytes(), "application/pdf")
    conv = api.req("POST", SALES, f"/orgs/{docs['org']}/assistant/conversations", json={}).json()["id"]
    docs["ai"].script = [tool_reply("search_files", {"query": "كم يوماً لإرجاع المنتج؟"}),
                         tool_reply("read_file", {"file_id": pdf["id"]}), text_reply("خلال أربعة عشر يوماً.")]
    out = api.req("POST", SALES, f"/orgs/{docs['org']}/assistant/conversations/{conv}/messages",
                  json={"content": "ما مدة الاسترجاع؟"}).json()
    assert [(c["name"], c["status"]) for c in out["tool_calls"]] == [("search_files", "executed"), ("read_file", "executed")]
    results = [json.loads(m["content"][0]["content"])["data"] for m in docs["ai"].requests[-1]["messages"]
               if m["role"] == "user" and isinstance(m["content"], list) and m["content"][0].get("type") == "tool_result"]
    assert results[0][0]["filename"] == "policy.pdf" and "أربعة عشر" in results[0][0]["text"]
    assert results[1]["total_chars"] > 100 and "Refund policy" in results[1]["text"] and results[1]["more"] is False


def test_an_agent_answers_from_its_own_knowledge_files_only(api, docs):
    org = docs["org"]
    policy = upload(api, docs, "returns.txt", "Returns: within 14 days, salary of the courier not refunded.".encode(),
                    "text/plain")
    upload(api, docs, "payroll.txt", "Salary of every employee: confidential figures.".encode(), "text/plain")
    agent = api.req("POST", OWNER, f"/orgs/{org}/agents", json={"name": "Returns helper", "description": ""}).json()["id"]
    url = f"/orgs/{org}/agents/{agent}"
    draft = {"instructions": "Answer questions about returns.", "tools": [], "route": "assistant", "max_steps": 3,
             "max_cost_usd": "0.10"}
    assert api.req("PUT", OWNER, url + "/draft", json=draft | {"knowledge": ["nope"]}).status_code == 422
    v = api.req("PUT", OWNER, url + "/draft", json=draft | {"knowledge": [policy["id"]]}).json()
    assert v["knowledge"] == [{"file_id": policy["id"], "filename": "returns.txt", "text_status": "ready"}]
    assert api.req("PUT", OWNER, url + "/draft", json=draft).json()["knowledge"][0]["file_id"] == policy["id"]  # kept

    # Attaching files means letting people who run the agent read them: it needs files.read.
    api.req("POST", OWNER, f"/orgs/{org}/roles", json={"key": "agent_builder", "name": "Agent builder",
                                                      "permissions": ["agents.manage", "agents.run", "ai.use"]})
    api.add_member(OWNER, org, CURATOR, "agent_builder")
    assert api.req("PUT", CURATOR, url + "/draft", json=draft | {"knowledge": [policy["id"]]}).status_code == 403

    api.req("POST", OWNER, url + "/tests", json={"name": "looks it up", "input": "How long for returns?",
                                                "expect_tools": ["search_knowledge"], "reply_contains": ["14"]})
    docs["ai"].script = [tool_reply("search_knowledge", {"query": "returns days"}), text_reply("Within 14 days.")]
    assert api.req("POST", OWNER, url + "/versions/1/test-runs").json()[0]["passed"] is True
    assert api.req("POST", OWNER, url + "/versions/1/publish").status_code == 200

    docs["ai"].script = [tool_reply("search_knowledge", {"query": "salary of employees"}), text_reply("Not covered.")]
    out = api.req("POST", SALES, url + "/runs", json={"input": "What are the salaries?"}).json()
    request = docs["ai"].requests[-1]
    assert {t["name"] for t in request["tools"]} == {"search_knowledge"}
    assert "reference documents" in request["system"]
    data = json.loads(request["messages"][-1]["content"][0]["content"])["data"]
    assert [p["filename"] for p in data] == ["returns.txt"]          # payroll.txt is never searched
    assert out["tool_calls"][0]["status"] == "executed"

    # The file deleted later shows as such; a new draft keeps the list.
    api.req("DELETE", OWNER, f"/orgs/{org}/files/{policy['id']}")
    versions = api.req("GET", OWNER, url).json()["versions"]
    assert versions[0]["knowledge"][0]["text_status"] == "deleted"
    assert api.req("PUT", OWNER, url + "/draft", json=draft).json()["number"] == 2
