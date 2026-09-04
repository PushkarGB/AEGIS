"""ExtractDocument capability: deterministic text extraction from normal text PDFs.

Uses PyMuPDF to extract text and structure deterministically from PDF documents.
OCR and vision/image analysis are explicitly not performed by this capability.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aegis.capabilities.base import (
    Capability,
    CapabilityContract,
    CapabilityKind,
    CapabilityMetadata,
)
from aegis.schemas import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityResultStatus,
    Observation,
)


def extract_document_text(document_path: str | Path) -> dict[str, Any]:
    """Deterministically extract text content and page metadata from a PDF.

    Parameters
    ----------
    document_path:
        Path to the PDF document to extract.

    Returns
    -------
    dict[str, Any]:
        Extracted text, page-by-page content, page count, and document metadata.
    """
    path = Path(document_path)
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")

    import pymupdf

    doc = pymupdf.open(str(path))
    page_count = len(doc)
    pages: list[dict[str, Any]] = []
    full_text_parts: list[str] = []

    for idx, page in enumerate(doc):
        text = page.get_text("text").strip()
        pages.append({
            "page_number": idx + 1,
            "text": text,
            "character_count": len(text),
        })
        if text:
            full_text_parts.append(f"--- Page {idx + 1} ---\n{text}")

    meta = doc.metadata or {}
    doc.close()

    full_text = "\n\n".join(full_text_parts)
    return {
        "text": full_text,
        "page_count": page_count,
        "pages": pages,
        "title": meta.get("title") or path.stem,
        "author": meta.get("author", ""),
        "subject": meta.get("subject", ""),
        "extraction_method": "pymupdf_text",
        "has_text": len(full_text.strip()) > 0,
    }


class ExtractDocumentCapability(Capability):
    """Deterministic document extraction entry point for normal text PDFs."""

    def __init__(self) -> None:
        self._metadata = CapabilityMetadata(
            name="extract_document",
            kind=CapabilityKind.TOOL,
            description="Extract text content and page metadata from text PDF documents deterministically.",
            input_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "document": {"type": "string", "description": "Path to document file"},
                        "document_path": {"type": "string", "description": "Alternative key for document path"},
                    },
                }
            ),
            output_contract=CapabilityContract(
                json_schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "page_count": {"type": "integer"},
                        "extraction_method": {"type": "string"},
                        "has_text": {"type": "boolean"},
                    },
                    "required": ["text", "page_count", "extraction_method"],
                }
            ),
            input_modalities=("document", "scanned_document"),
        )

    @property
    def metadata(self) -> CapabilityMetadata:
        return self._metadata

    def execute(self, request: CapabilityRequest) -> CapabilityResult:
        doc_path = request.inputs.get("document") or request.inputs.get("document_path")
        if not doc_path or not isinstance(doc_path, str):
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error="Missing required 'document' or 'document_path' string input.",
            )

        try:
            extraction = extract_document_text(doc_path)
        except Exception as exc:
            return CapabilityResult(
                request_id=request.request_id,
                status=CapabilityResultStatus.FAILED,
                error=f"Document extraction failed: {exc}",
            )

        observation = Observation(
            source="extract_document",
            kind="document_extracted",
            summary=(
                f"Extracted {len(extraction['text'])} characters from {extraction['page_count']} pages "
                f"via {extraction['extraction_method']}."
            ),
            data={
                "page_count": extraction["page_count"],
                "character_count": len(extraction["text"]),
                "extraction_method": extraction["extraction_method"],
                "title": extraction["title"],
                "has_text": extraction["has_text"],
            },
            request_id=request.request_id,
        )

        return CapabilityResult(
            request_id=request.request_id,
            status=CapabilityResultStatus.SUCCEEDED,
            output=extraction,
            observations=[observation],
        )
