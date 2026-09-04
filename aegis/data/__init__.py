"""Local data, artifacts, and fixture paths, plus document type identification."""

from .document_type import (
    DocumentCategory,
    DocumentTypeResult,
    identify_document_type,
)

__all__ = [
    "DocumentCategory",
    "DocumentTypeResult",
    "identify_document_type",
]
