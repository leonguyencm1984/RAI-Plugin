"""
Local source extraction helpers for the rai plugin.

Mirrors the logic from the RAI-Platform backend's extractors.py,
source_service.py, and archive_ingest.py — copied here so the
plugin runs without importing the backend package.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path

_MAX_TEXT_BYTES = 1_048_576      # 1 MB  — text / code files
_MAX_OFFICE_BYTES = 10_485_760   # 10 MB — PDF, DOCX, XLSX
_MAX_RAW_TEXT_CHARS = 80_000     # ~20 K tokens — matches wiki_compiler


# ---------------------------------------------------------------------------
# File extractors (bytes-in, text-out)
# ---------------------------------------------------------------------------

def extract_xlsx_bytes(data: bytes) -> str:
    """Extract text from Excel (.xlsx / .xls) bytes."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet in wb.worksheets:
        rows: list[str] = []
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(c.strip() for c in cells):
                rows.append(" | ".join(cells))
        if rows:
            parts.append(f"## {sheet.title}\n\n" + "\n".join(rows))
    wb.close()
    return "\n\n".join(parts)


def extract_csv_bytes(data: bytes) -> str:
    """Decode CSV bytes to UTF-8 string."""
    return data.decode("utf-8", errors="replace")


def extract_pdf_bytes(data: bytes) -> str:
    """Extract text from PDF bytes using pypdf."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            parts.append(text)
    return "\n".join(parts)


def extract_docx_bytes(data: bytes) -> str:
    """Extract text from DOCX bytes using python-docx."""
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n".join(para.text for para in doc.paragraphs if para.text)


# ---------------------------------------------------------------------------
# URL extractor
# ---------------------------------------------------------------------------

async def extract_url(url: str) -> str:
    """Fetch a URL and extract body text using httpx + BeautifulSoup."""
    import httpx
    from bs4 import BeautifulSoup

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    body = soup.body or soup
    return body.get_text(separator="\n")


# ---------------------------------------------------------------------------
# Text cleanup
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Strip and deduplicate excessive whitespace."""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Single-file dispatcher
# ---------------------------------------------------------------------------

def file_to_text(path: str, data: bytes) -> str | None:
    """Convert file bytes to plain text, or return None to skip."""
    suffix = Path(path).suffix.lower()
    max_bytes = _MAX_OFFICE_BYTES if suffix in (".pdf", ".docx", ".xlsx", ".xls") else _MAX_TEXT_BYTES
    if len(data) > max_bytes:
        return None

    if suffix in (".md", ".markdown", ".txt"):
        return data.decode("utf-8", errors="replace")

    if suffix == ".pdf":
        try:
            text = extract_pdf_bytes(data)
            return text if text.strip() else None
        except Exception:
            return None

    if suffix == ".docx":
        try:
            text = extract_docx_bytes(data)
            return text if text.strip() else None
        except Exception:
            return None

    if suffix in (".xlsx", ".xls"):
        try:
            text = extract_xlsx_bytes(data)
            return text if text.strip() else None
        except Exception:
            return None

    if suffix == ".csv":
        try:
            text = extract_csv_bytes(data)
            return text if text.strip() else None
        except Exception:
            return None

    return None  # unsupported extension


# ---------------------------------------------------------------------------
# Folder / zip ingestion
# ---------------------------------------------------------------------------

def ingest_folder(folder_path: Path) -> str:
    """Walk a directory and concatenate text from all supported files."""
    parts: list[str] = []
    for fpath in sorted(folder_path.rglob("*")):
        if not fpath.is_file():
            continue
        data = fpath.read_bytes()
        relpath = str(fpath.relative_to(folder_path))
        text = file_to_text(relpath, data)
        if text and text.strip():
            parts.append(f"## {relpath}\n\n{clean_text(text)}")
    return "\n\n".join(parts)


def ingest_zip(zip_path: Path) -> str:
    """Extract a zip archive and concatenate text from supported files."""
    parts: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            if name.endswith("/"):
                continue
            data = zf.read(name)
            text = file_to_text(name, data)
            if text and text.strip():
                parts.append(f"## {name}\n\n{clean_text(text)}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Deterministic local ID
# ---------------------------------------------------------------------------

def local_source_id(content: str) -> str:
    """Stable ID for deduplication: 'local-' + first 8 chars of SHA1."""
    return "local-" + hashlib.sha1(content.encode()).hexdigest()[:8]
