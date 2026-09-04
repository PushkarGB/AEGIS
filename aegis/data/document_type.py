"""Deterministic document-type identification from file content.

Inspects magic bytes and internal structure to determine the true file type,
independent of filename or extension.  This is a pre-routing utility that runs
before the Controller exists — it is NOT a Capability.

Architecture reference (ARCHITECTURE.md §2 Determinism):
  Prefer deterministic implementation for: file-type detection
"""

from __future__ import annotations

import zipfile
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class DocumentCategory(StrEnum):
    """High-level document categories recognized by the prototype."""

    PDF = "pdf"
    SPREADSHEET = "spreadsheet"
    IMAGE = "image"
    PRESENTATION = "presentation"
    WORD_DOCUMENT = "word_document"
    TEXT = "text"
    UNKNOWN = "unknown"


# Mapping from DocumentCategory to canonical MIME types
_CATEGORY_MIME: dict[DocumentCategory, str] = {
    DocumentCategory.PDF: "application/pdf",
    DocumentCategory.SPREADSHEET: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    DocumentCategory.IMAGE: "image/png",  # refined per-format below
    DocumentCategory.PRESENTATION: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    DocumentCategory.WORD_DOCUMENT: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocumentCategory.TEXT: "text/plain",
    DocumentCategory.UNKNOWN: "application/octet-stream",
}

# Image format-specific MIME types
_IMAGE_MIME: dict[str, str] = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
}


class DocumentTypeResult(BaseModel):
    """Structured result of deterministic document-type identification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: DocumentCategory
    mime_type: str = Field(min_length=1)
    extension: str = Field(default="", description="Observed file extension (informational, not authoritative)")
    page_count: int | None = Field(default=None, ge=0)
    has_extractable_text: bool | None = None
    file_size_bytes: int = Field(ge=0)
    detection_method: str = Field(min_length=1)
    details: dict[str, object] = Field(default_factory=dict)


def identify_document_type(path: Path | str) -> DocumentTypeResult:
    """Identify the document type by inspecting file content.

    Reads magic bytes and internal structure to determine the true file type.
    Never relies on the filename or extension for the authoritative type.

    Parameters
    ----------
    path:
        Absolute or relative path to the file to inspect.

    Returns
    -------
    DocumentTypeResult:
        Structured identification result with category, MIME type, and
        content characteristics.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a regular file: {path}")

    file_size = path.stat().st_size
    extension = path.suffix.lower()

    # Read magic bytes (first 16 bytes is sufficient for all checks)
    with open(path, "rb") as f:
        header = f.read(16)

    if len(header) == 0:
        return DocumentTypeResult(
            category=DocumentCategory.UNKNOWN,
            mime_type="application/octet-stream",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="empty_file",
        )

    # --- PDF: starts with %PDF ---
    if header[:4] == b"%PDF":
        return _identify_pdf(path, extension, file_size)

    # --- ZIP-based formats (XLSX, DOCX, PPTX) ---
    if header[:2] == b"PK":
        return _identify_zip_based(path, extension, file_size)

    # --- Image formats ---
    image_result = _identify_image(header, extension, file_size)
    if image_result is not None:
        return image_result

    # --- OLE2 Compound Document (legacy .xls, .doc, .ppt) ---
    if header[:4] == b"\xd0\xcf\x11\xe0":
        return _identify_ole2(extension, file_size)

    # --- Plain text / CSV heuristic ---
    text_result = _identify_text(path, extension, file_size)
    if text_result is not None:
        return text_result

    return DocumentTypeResult(
        category=DocumentCategory.UNKNOWN,
        mime_type="application/octet-stream",
        extension=extension,
        file_size_bytes=file_size,
        detection_method="unrecognized_header",
    )


def _identify_pdf(path: Path, extension: str, file_size: int) -> DocumentTypeResult:
    """Inspect a PDF file for page count and extractable text."""
    page_count: int | None = None
    has_text: bool | None = None
    details: dict[str, object] = {}

    try:
        import pymupdf

        doc = pymupdf.open(str(path))
        page_count = len(doc)
        details["pdf_version"] = doc.metadata.get("format", "") if doc.metadata else ""

        # Check if any page has meaningful extractable text
        text_chars = 0
        for page_idx in range(min(page_count, 5)):  # sample first 5 pages
            page_text = doc[page_idx].get_text("text")
            text_chars += len(page_text.strip())

        has_text = text_chars > 20  # threshold: more than trivial whitespace
        details["sampled_text_chars"] = text_chars
        doc.close()
    except Exception as exc:
        details["pdf_inspection_error"] = str(exc)

    return DocumentTypeResult(
        category=DocumentCategory.PDF,
        mime_type="application/pdf",
        extension=extension,
        page_count=page_count,
        has_extractable_text=has_text,
        file_size_bytes=file_size,
        detection_method="magic_bytes_pdf",
        details=details,
    )


def _identify_zip_based(path: Path, extension: str, file_size: int) -> DocumentTypeResult:
    """Inspect a ZIP-based file for Office XML format markers."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())

            # XLSX: contains xl/ directory
            if any(n.startswith("xl/") for n in names):
                return DocumentTypeResult(
                    category=DocumentCategory.SPREADSHEET,
                    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    extension=extension,
                    file_size_bytes=file_size,
                    detection_method="zip_content_xl",
                )

            # DOCX: contains word/ directory
            if any(n.startswith("word/") for n in names):
                return DocumentTypeResult(
                    category=DocumentCategory.WORD_DOCUMENT,
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    extension=extension,
                    file_size_bytes=file_size,
                    detection_method="zip_content_word",
                )

            # PPTX: contains ppt/ directory
            if any(n.startswith("ppt/") for n in names):
                return DocumentTypeResult(
                    category=DocumentCategory.PRESENTATION,
                    mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    extension=extension,
                    file_size_bytes=file_size,
                    detection_method="zip_content_ppt",
                )

    except zipfile.BadZipFile:
        pass

    return DocumentTypeResult(
        category=DocumentCategory.UNKNOWN,
        mime_type="application/zip",
        extension=extension,
        file_size_bytes=file_size,
        detection_method="zip_unknown_content",
    )


def _identify_image(header: bytes, extension: str, file_size: int) -> DocumentTypeResult | None:
    """Check magic bytes for common image formats."""
    # PNG
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return DocumentTypeResult(
            category=DocumentCategory.IMAGE,
            mime_type="image/png",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="magic_bytes_png",
        )

    # JPEG
    if header[:3] == b"\xff\xd8\xff":
        return DocumentTypeResult(
            category=DocumentCategory.IMAGE,
            mime_type="image/jpeg",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="magic_bytes_jpeg",
        )

    # GIF
    if header[:4] in (b"GIF8",):
        return DocumentTypeResult(
            category=DocumentCategory.IMAGE,
            mime_type="image/gif",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="magic_bytes_gif",
        )

    # BMP
    if header[:2] == b"BM":
        return DocumentTypeResult(
            category=DocumentCategory.IMAGE,
            mime_type="image/bmp",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="magic_bytes_bmp",
        )

    # WEBP (RIFF....WEBP)
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return DocumentTypeResult(
            category=DocumentCategory.IMAGE,
            mime_type="image/webp",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="magic_bytes_webp",
        )

    # TIFF
    if header[:4] in (b"II\x2a\x00", b"MM\x00\x2a"):
        return DocumentTypeResult(
            category=DocumentCategory.IMAGE,
            mime_type="image/tiff",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="magic_bytes_tiff",
        )

    return None


def _identify_ole2(extension: str, file_size: int) -> DocumentTypeResult:
    """Identify legacy OLE2 compound documents (.xls, .doc, .ppt)."""
    # We cannot reliably distinguish XLS/DOC/PPT from the OLE2 header alone
    # without parsing the compound document structure. Use extension as a hint.
    if extension in (".xls",):
        return DocumentTypeResult(
            category=DocumentCategory.SPREADSHEET,
            mime_type="application/vnd.ms-excel",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="ole2_header_xls_hint",
        )
    if extension in (".doc",):
        return DocumentTypeResult(
            category=DocumentCategory.WORD_DOCUMENT,
            mime_type="application/msword",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="ole2_header_doc_hint",
        )
    if extension in (".ppt",):
        return DocumentTypeResult(
            category=DocumentCategory.PRESENTATION,
            mime_type="application/vnd.ms-powerpoint",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="ole2_header_ppt_hint",
        )

    return DocumentTypeResult(
        category=DocumentCategory.UNKNOWN,
        mime_type="application/x-ole-storage",
        extension=extension,
        file_size_bytes=file_size,
        detection_method="ole2_header_unknown",
    )


def _identify_text(path: Path, extension: str, file_size: int) -> DocumentTypeResult | None:
    """Heuristic check for plain text or CSV files."""
    # Only attempt for reasonably small files to avoid loading large binaries
    if file_size > 10 * 1024 * 1024:  # 10 MB limit
        return None

    try:
        sample = path.read_bytes(4096) if file_size > 4096 else path.read_bytes()
        # Check if content is mostly printable ASCII/UTF-8
        try:
            text = sample.decode("utf-8")
        except UnicodeDecodeError:
            return None

        # Count non-printable characters (excluding common whitespace)
        non_printable = sum(
            1 for ch in text
            if not ch.isprintable() and ch not in ("\n", "\r", "\t")
        )
        if non_printable > len(text) * 0.05:  # more than 5% non-printable
            return None

        # CSV heuristic: check for comma or tab delimiters with consistent structure
        lines = text.strip().split("\n")
        if extension in (".csv", ".tsv") or (
            len(lines) >= 2
            and all("," in line or "\t" in line for line in lines[:5])
        ):
            return DocumentTypeResult(
                category=DocumentCategory.SPREADSHEET,
                mime_type="text/csv",
                extension=extension,
                file_size_bytes=file_size,
                detection_method="text_csv_heuristic",
            )

        return DocumentTypeResult(
            category=DocumentCategory.TEXT,
            mime_type="text/plain",
            extension=extension,
            file_size_bytes=file_size,
            detection_method="text_content_heuristic",
        )

    except Exception:
        return None
