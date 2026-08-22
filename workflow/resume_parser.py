"""Deterministic, offline resume text extraction and conservative fact parsing."""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MEDIA_TYPE = "application/pdf"


@dataclass(frozen=True)
class ParsedFact:
    namespace: str
    field: str
    value: str
    source_page: int
    source_start: int
    source_end: int
    confidence: float


@dataclass(frozen=True)
class ParsedResume:
    text: str
    page_count: int
    facts: tuple[ParsedFact, ...]


def _facts(text: str, page_starts: list[int]) -> tuple[ParsedFact, ...]:
    patterns = (
        ("contact", "email", r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])", 0.99),
        ("contact", "phone", r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)", 0.88),
        ("links", "linkedin", r"(?i)https?://(?:www\.)?linkedin\.com/in/[\w%.-]+/?", 0.98),
    )
    found: list[ParsedFact] = []
    for namespace, field, pattern, confidence in patterns:
        match = re.search(pattern, text)
        if match:
            start, end = match.span()
            page = sum(offset <= start for offset in page_starts)
            found.append(ParsedFact(namespace, field, match.group().strip(), max(page, 1), start, end, confidence))
    return tuple(found)


def parse_resume(data: bytes, media_type: str, max_pages: int = 10) -> ParsedResume:
    """Extract text locally. Offsets refer to the returned normalized text."""
    if media_type == PDF_MEDIA_TYPE:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # dependency is only needed for PDF input
            raise RuntimeError("PDF parsing requires pypdf") from exc
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > max_pages:
            raise ValueError(f"resume exceeds page limit of {max_pages}")
        pages = [(page.extract_text() or "").replace("\x00", "") for page in reader.pages]
    elif media_type == DOCX_MEDIA_TYPE:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
        except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise ValueError("invalid DOCX document") from exc
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        pages, current = [], []
        for element in root.iter():
            if element.tag == ns + "t" and element.text:
                current.append(element.text)
            elif element.tag == ns + "br" and element.attrib.get(ns + "type") == "page":
                pages.append(" ".join(current)); current = []
            elif element.tag == ns + "p" and current:
                current.append("\n")
        pages.append(" ".join(current))
        if len(pages) > max_pages:
            raise ValueError(f"resume exceeds page limit of {max_pages}")
    else:
        raise ValueError("unsupported resume media type")
    text = "\f".join(pages)
    starts, position = [], 0
    for page in pages:
        starts.append(position)
        position += len(page) + 1
    return ParsedResume(text, len(pages), _facts(text, starts))
