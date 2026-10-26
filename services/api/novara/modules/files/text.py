"""Text inside uploaded files, for the assistant and agents: extraction, passages and ranked search.

Text is extracted from PDF (pypdf), Word .docx (read directly from its XML) and plain text, CSV,
JSON and XML, then cut into passages of about CHUNK_CHARS characters. Search is lexical, not
vector-based: words are normalised (Arabic diacritics, tatweel and letter variants such as
أ/إ/آ → ا, ى → ي, ة → ه; Arabic-Indic digits; case), the definite article is set aside, and
passages are ranked with BM25. So "الفاتورة" finds "فاتورة" and "الفاتوره", but not a synonym.

PDFs made by browsers and office programs often store Arabic as shaped glyphs in visual order;
the extracted lines are put back in reading order (see _logical_line).
"""

from __future__ import annotations

import io
import math
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass
from xml.etree import ElementTree

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from novara.modules.files.models import FileChunk, StoredFile

CHUNK_CHARS = 900
MAX_TEXT_CHARS = 2_000_000
MAX_PDF_PAGES = 300
MAX_DOCX_XML_BYTES = 30 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
LAZY_PER_SEARCH = 5  # files uploaded before indexing existed are read a few at a time, on search

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/csv")


class TextUnavailable(ValueError):
    """The file has no text we can read (an image, a scanned PDF, an unsupported or damaged file)."""


# ---------------------------------------------------------------- extraction


def kind_of(content_type: str, filename: str) -> str | None:
    name = filename.lower()
    if content_type == PDF or name.endswith(".pdf"):
        return "pdf"
    if content_type == DOCX or name.endswith(".docx"):
        return "docx"
    if content_type.startswith(TEXT_TYPES) or name.endswith((".txt", ".csv", ".md", ".json", ".xml")):
        return "text"
    return None


def extract(data: bytes, content_type: str, filename: str) -> str:
    kind = kind_of(content_type, filename)
    if kind is None:
        raise TextUnavailable("this kind of file has no readable text")
    if len(data) > MAX_FILE_BYTES:
        raise TextUnavailable("the file is too large to read")
    text = {"pdf": _pdf_text, "docx": _docx_text, "text": _plain_text}[kind](data)
    text = unicodedata.normalize("NFKC", text).replace("\u0000", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise TextUnavailable("no text found (a scanned document needs OCR, which isn't available)")
    return text[:MAX_TEXT_CHARS]


def _plain_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1256", errors="replace")  # older Arabic Windows files


_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_SHAPED = re.compile(r"[ﭐ-﷿ﹰ-﻿]")
_LTR = re.compile(r"[A-Za-z0-9٠-٩]")
_LEADING_MARKS = re.compile(r"^([.،؛:!?؟,)]+)(.+)$")


def _logical_line(line: str) -> str:
    """A mostly-Arabic line extracted in visual (left-to-right) order, put back in reading order:
    the words are reversed, runs of Latin words and numbers keep their own order, and sentence marks
    that visual order put in front of a word go back after it."""
    if len(_ARABIC.findall(line)) <= len(re.findall(r"[A-Za-z]", line)):
        return line
    words, out, ltr_run = line.split(), [], []
    for word in reversed(words):
        if _LTR.search(word) and not _ARABIC.search(word):
            ltr_run.append(word)
            continue
        out.extend(reversed(ltr_run))
        ltr_run = []
        marks = _LEADING_MARKS.match(word)
        out.append(marks.group(2) + marks.group(1) if marks and _ARABIC.search(marks.group(2)) else word)
    out.extend(reversed(ltr_run))
    return " ".join(out)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise TextUnavailable("the PDF is password-protected")
        pages = [page.extract_text() or "" for page in reader.pages[:MAX_PDF_PAGES]]
    except TextUnavailable:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
        raise TextUnavailable(f"the PDF couldn't be read: {type(e).__name__}") from e
    text = "\n\n".join(pages)
    if _SHAPED.search(text):  # shaped Arabic glyphs: the producer wrote lines in visual order
        text = "\n".join(_logical_line(line) for line in text.splitlines())
    return text


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:  # a zip bomb, or simply too big
                raise TextUnavailable("the document is too large to read")
            root = ElementTree.fromstring(z.read(info))
    except TextUnavailable:
        raise
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise TextUnavailable("the Word file couldn't be read") from e
    paragraphs = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


# ---------------------------------------------------------------- words


_DIACRITICS = re.compile(r"[ً-ٰٟۖ-ۭـ]")  # harakat, dagger alef, Quranic marks, tatweel
_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي",
                          **{chr(0x0660 + i): str(i) for i in range(10)}, **{chr(0x06F0 + i): str(i) for i in range(10)}})
_ARTICLE = ("وال", "بال", "كال", "فال", "لل", "ال")
_WORD = re.compile(r"\w+")
STOPWORDS = frozenset({
    "في", "من", "علي", "الي", "عن", "ما", "ماذا", "هل", "كيف", "هذا", "هذه", "ذلك", "تلك", "التي", "الذي", "او", "ثم",
    "مع", "كل", "قد", "لا", "لم", "لن", "ان", "اذا", "هو", "هي", "نحن", "انا", "عند", "بعد", "قبل", "حتي", "كان",
    "the", "a", "an", "of", "to", "and", "or", "is", "are", "in", "on", "for", "with", "what", "how", "do", "does",
    "be", "by", "it", "this", "that", "at", "as", "from", "can", "i", "we", "you", "our", "my",
})


def normalise(text: str) -> str:
    return _DIACRITICS.sub("", unicodedata.normalize("NFKC", text)).translate(_LETTERS).lower()


def _stem(word: str) -> str:
    for article in _ARTICLE:
        if word.startswith(article) and len(word) - len(article) >= 2:
            return word[len(article):]
    if word.isascii() and len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(normalise(text))]


def query_terms(query: str) -> list[str]:
    seen: dict[str, None] = {}
    for w in words(query):
        if w not in STOPWORDS and len(w) >= 2:
            seen.setdefault(w, None)
    return list(seen)[:12]


# ---------------------------------------------------------------- passages


def chunks(text: str) -> list[str]:
    """Passages of about CHUNK_CHARS characters, cut between paragraphs, else sentences, else words."""
    out: list[str] = []
    current = ""
    for para in (p.strip() for p in re.split(r"\n\s*\n|\n", text)):
        if not para:
            continue
        while len(para) > CHUNK_CHARS:
            cut = max(para.rfind(". ", 0, CHUNK_CHARS), para.rfind("، ", 0, CHUNK_CHARS), para.rfind(" ", 0, CHUNK_CHARS))
            cut = cut + 1 if cut > CHUNK_CHARS // 3 else CHUNK_CHARS
            if current:
                out.append(current)
                current = ""
            out.append(para[:cut].strip())
            para = para[cut:].strip()
        if current and len(current) + len(para) + 1 > CHUNK_CHARS:
            out.append(current)
            current = ""
        current = f"{current}\n{para}" if current else para
    if current:
        out.append(current)
    return out


def index_file(db: Session, storage, f: StoredFile, data: bytes | None = None) -> None:
    """Read the file's text into passages (replacing any before). Never raises for unreadable files:
    the file's text_status says what happened."""
    db.execute(delete(FileChunk).where(FileChunk.file_id == f.id))
    if kind_of(f.content_type, f.filename) is None:
        f.text_status, f.text_chars = "none", 0
        return
    try:
        text = extract(data if data is not None else storage.get(f.storage_key), f.content_type, f.filename)
    except TextUnavailable:
        f.text_status, f.text_chars = "none", 0
        return
    except Exception:  # storage trouble or a library failure: say so, and try again on the next search
        f.text_status, f.text_chars = "failed", 0
        return
    for position, passage in enumerate(chunks(text)):
        db.add(FileChunk(file_id=f.id, position=position, text=passage, terms=" ".join(words(passage))))
    f.text_status, f.text_chars = "ready", len(text)


def index_pending(db: Session, storage, file_ids: list[str] | None = None, limit: int = LAZY_PER_SEARCH) -> None:
    q = select(StoredFile).where(StoredFile.text_status.in_(("pending", "failed")))
    if file_ids is not None:
        q = q.where(StoredFile.id.in_(file_ids))
    for f in db.scalars(q.order_by(StoredFile.created_at.desc()).limit(limit)):
        index_file(db, storage, f)
    db.flush()


@dataclass(frozen=True)
class Passage:
    file_id: str
    filename: str
    position: int
    score: float
    text: str


K1, B = 1.2, 0.75
MAX_CANDIDATES = 3000


def search(db: Session, query: str, *, file_ids: list[str] | None = None, limit: int = 8) -> list[Passage]:
    """The passages that best match the query's words (BM25), best first; ``file_ids`` limits the files searched."""
    terms = query_terms(query)
    if not terms or file_ids == []:
        return []
    scope = select(FileChunk.id)
    if file_ids is not None:
        scope = scope.where(FileChunk.file_id.in_(file_ids))
    total = db.scalar(select(func.count()).select_from(scope.subquery())) or 0
    if not total:
        return []
    q = (select(FileChunk.id, FileChunk.file_id, FileChunk.position, FileChunk.text, FileChunk.terms)
         .where(or_(*[FileChunk.terms.contains(t, autoescape=True) for t in terms])))
    if file_ids is not None:
        q = q.where(FileChunk.file_id.in_(file_ids))
    rows = db.execute(q.limit(MAX_CANDIDATES)).all()
    if not rows:
        return []
    wanted = set(terms)
    lengths, counts = [], []
    for r in rows:
        toks = r.terms.split()
        lengths.append(len(toks))
        counts.append(Counter(t for t in toks if t in wanted))
    avg = sum(lengths) / len(lengths)
    df = Counter(t for c in counts for t in c)
    scored = []
    for row, length, tfs in zip(rows, lengths, counts):
        score = 0.0
        for t, tf in tfs.items():
            idf = math.log(1 + (total - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avg))
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda s: (-s[0], s[1].position))
    names = dict(db.execute(select(StoredFile.id, StoredFile.filename)
                            .where(StoredFile.id.in_({row.file_id for _, row in scored[:limit]}))).all())
    return [Passage(row.file_id, names.get(row.file_id, ""), row.position, round(score, 3), row.text)
            for score, row in scored[:limit]]


def read(db: Session, f: StoredFile, start: int = 0, length: int = 6000) -> tuple[str, int]:
    """Part of a file's text (from its passages), and the total length."""
    passages = db.scalars(select(FileChunk.text).where(FileChunk.file_id == f.id).order_by(FileChunk.position)).all()
    text = "\n\n".join(passages)
    return text[start:start + length], len(text)
