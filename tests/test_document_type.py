"""Unit and regression tests for deterministic document-type identification."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from aegis.data import DocumentCategory, DocumentTypeResult, identify_document_type
from demo.fixtures import SYNTHETIC_EQUIPMENT_WORKBOOK, SYNTHETIC_INSPECTION_REPORT_PDF


def test_identify_pdf_fixture():
    """Verify that the synthetic inspection report PDF is correctly identified."""
    assert SYNTHETIC_INSPECTION_REPORT_PDF.exists()
    result = identify_document_type(SYNTHETIC_INSPECTION_REPORT_PDF)

    assert isinstance(result, DocumentTypeResult)
    assert result.category == DocumentCategory.PDF
    assert result.mime_type == "application/pdf"
    assert result.detection_method == "magic_bytes_pdf"
    assert result.page_count == 2
    assert result.has_extractable_text is True
    assert result.file_size_bytes > 0


def test_identify_spreadsheet_fixture():
    """Verify that the synthetic equipment spreadsheet is correctly identified via ZIP contents."""
    assert SYNTHETIC_EQUIPMENT_WORKBOOK.exists()
    result = identify_document_type(SYNTHETIC_EQUIPMENT_WORKBOOK)

    assert isinstance(result, DocumentTypeResult)
    assert result.category == DocumentCategory.SPREADSHEET
    assert "spreadsheetml" in result.mime_type
    assert result.detection_method == "zip_content_xl"
    assert result.file_size_bytes > 0


def test_identify_renamed_file_by_content_not_extension(tmp_path: Path):
    """Prove that document type detection uses content magic bytes, ignoring deceptive extensions."""
    # Create a PDF file disguised as .txt
    disguised_pdf = tmp_path / "disguised.txt"
    disguised_pdf.write_bytes(SYNTHETIC_INSPECTION_REPORT_PDF.read_bytes())

    result = identify_document_type(disguised_pdf)
    assert result.category == DocumentCategory.PDF
    assert result.mime_type == "application/pdf"
    assert result.extension == ".txt"  # Informational only
    assert result.has_extractable_text is True

    # Create an XLSX disguised as .pdf
    disguised_xlsx = tmp_path / "disguised.pdf"
    disguised_xlsx.write_bytes(SYNTHETIC_EQUIPMENT_WORKBOOK.read_bytes())

    result_xlsx = identify_document_type(disguised_xlsx)
    assert result_xlsx.category == DocumentCategory.SPREADSHEET
    assert "spreadsheetml" in result_xlsx.mime_type
    assert result_xlsx.detection_method == "zip_content_xl"


def test_identify_image_types(tmp_path: Path):
    """Verify detection of image headers."""
    # PNG
    png_file = tmp_path / "test.dat"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    res_png = identify_document_type(png_file)
    assert res_png.category == DocumentCategory.IMAGE
    assert res_png.mime_type == "image/png"

    # JPEG
    jpg_file = tmp_path / "photo.bin"
    jpg_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    res_jpg = identify_document_type(jpg_file)
    assert res_jpg.category == DocumentCategory.IMAGE
    assert res_jpg.mime_type == "image/jpeg"


def test_identify_unknown_file(tmp_path: Path):
    """Verify graceful handling of unknown binary payloads."""
    bin_file = tmp_path / "unknown.bin"
    bin_file.write_bytes(b"\x00\x01\x02\x03\x04\x05\x06\x07" * 4)

    result = identify_document_type(bin_file)
    assert result.category == DocumentCategory.UNKNOWN
    assert result.mime_type == "application/octet-stream"


def test_identify_nonexistent_file():
    """Verify FileNotFoundError for missing paths."""
    with pytest.raises(FileNotFoundError):
        identify_document_type(Path("non_existent_file_12345.xyz"))
