"""Deterministic fixtures for AEGIS demonstrations and automated tests."""

from __future__ import annotations

from pathlib import Path
import openpyxl
import pymupdf

FIXTURES_DIR = Path(__file__).resolve().parent
SYNTHETIC_EQUIPMENT_WORKBOOK = FIXTURES_DIR / "synthetic_equipment_readings.xlsx"
SYNTHETIC_INSPECTION_REPORT_PDF = FIXTURES_DIR / "inspection_report.pdf"

EQUIPMENT_DATA = [
    ("EQ-001", 4.2, 4.0),
    ("EQ-001", 4.6, 4.0),
    ("EQ-002", 2.6, 3.0),
    ("EQ-002", 2.8, 3.0),
    ("EQ-003", 5.1, 4.8),
    ("EQ-003", 4.9, 4.8),
    ("EQ-004", 3.2, 3.5),
    ("EQ-004", 3.4, 3.5),
    ("EQ-005", 6.0, 5.5),
    ("EQ-005", 5.8, 5.5),
]

EXPECTED_COMPUTATION_RECORDS = [
    {
        "equipment_id": "EQ-001",
        "average_measured_thickness": 4.4,
        "min_acceptable_thickness": 4.0,
        "below_min_acceptable_thickness": False,
    },
    {
        "equipment_id": "EQ-002",
        "average_measured_thickness": 2.7,
        "min_acceptable_thickness": 3.0,
        "below_min_acceptable_thickness": True,
    },
    {
        "equipment_id": "EQ-003",
        "average_measured_thickness": 5.0,
        "min_acceptable_thickness": 4.8,
        "below_min_acceptable_thickness": False,
    },
    {
        "equipment_id": "EQ-004",
        "average_measured_thickness": 3.3,
        "min_acceptable_thickness": 3.5,
        "below_min_acceptable_thickness": True,
    },
    {
        "equipment_id": "EQ-005",
        "average_measured_thickness": 5.9,
        "min_acceptable_thickness": 5.5,
        "below_min_acceptable_thickness": False,
    },
]

EXPECTED_INSPECTION_TEXT_SNIPPETS = [
    "EQUIPMENT INTEGRITY INSPECTION REPORT",
    "Asset ID: PUMP-104B",
    "Asset ID: TANK-301A",
    "Asset ID: PIPE-EX-12",
    "Vibration level exceeds ISO standard 10816-3 threshold (7.8 mm/s RMS).",
    "External shell wall thickness measured at 3.1 mm against 4.0 mm nominal minimum.",
    "Flange gasket minor weeping observed at Joint #4; within operational tolerance.",
    "Inspector: J. Doe (ID: MECH-8821)",
]

EXPECTED_DRAFT_APPROVAL_NOTE = {
    "title": "APPROVAL NOTE: Equipment Integrity Inspection Review",
    "document_reference": "inspection_report.pdf",
    "approval_status": "DRAFT — PENDING OPERATOR APPROVAL",
    "key_findings": [
        "PUMP-104B: Vibration level exceeds ISO standard 10816-3 threshold at 7.8 mm/s RMS.",
        "TANK-301A: External shell wall thickness is 3.1 mm, which is below the 4.0 mm nominal minimum.",
        "PIPE-EX-12: Flange gasket minor weeping at Joint #4 is within operational limits.",
    ],
    "supporting_observations": [
        "PUMP-104B inboard bearing temperature elevated to 68C.",
        "TANK-301A localized corrosion detected on bottom ring segment 3.",
        "Inspection conducted by Certified Mechanical Inspector J. Doe on 2026-08-28.",
    ],
    "recommendations": [
        "Immediate vibration balancing and bearing replacement for PUMP-104B.",
        "Schedule ultrasonic non-destructive testing and derate pressure for TANK-301A pending repair.",
        "Monitor Joint #4 on PIPE-EX-12 during next planned quarterly turnaround.",
    ],
    "summary": "Critical integrity concerns identified for PUMP-104B and TANK-301A requiring operator sign-off.",
}


def create_synthetic_equipment_spreadsheet(target_path: Path | str | None = None) -> Path:
    """Create the deterministic synthetic equipment readings workbook."""
    path = Path(target_path) if target_path else SYNTHETIC_EQUIPMENT_WORKBOOK
    path.parent.mkdir(parents=True, exist_ok=True)

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Inspection Readings"
    sheet.append(
        [
            "Equipment_ID",
            "Measured_Thickness",
            "Min_Acceptable_Thickness",
        ]
    )
    for row in EQUIPMENT_DATA:
        sheet.append(list(row))

    workbook.save(path)
    return path


def create_synthetic_inspection_report(target_path: Path | str | None = None) -> Path:
    """Create a deterministic synthetic inspection report text PDF."""
    path = Path(target_path) if target_path else SYNTHETIC_INSPECTION_REPORT_PDF
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open()

    # Page 1: Header and Executive Summary
    page1 = doc.new_page(width=595, height=842)  # A4
    text1 = """EQUIPMENT INTEGRITY INSPECTION REPORT
Ref No: IR-2026-08-992A
Facility: Refinery Unit 3 - Hydrocracker Complex
Inspection Date: 2026-08-28
Lead Inspector: J. Doe (ID: MECH-8821)
Scope: Quarterly Statutory Mechanical Integrity Survey

EXECUTIVE SUMMARY
This report details the non-destructive testing, visual examination, and vibration
diagnostics performed on critical mechanical assets within Hydrocracker Complex Unit 3.
Two assets require priority maintenance intervention. One asset exhibits acceptable
minor wear within operational tolerance.

ASSET SUMMARY TABLE
-------------------------------------------------------------------------------
Asset ID       Type             Condition Rating   Priority
-------------------------------------------------------------------------------
PUMP-104B      Centrifugal Pump CRITICAL           Priority 1 (Immediate)
TANK-301A      Storage Vessel   DEGRADED           Priority 2 (High)
PIPE-EX-12     Process Piping   ACCEPTABLE         Routine Monitor
-------------------------------------------------------------------------------
"""
    page1.insert_text((50, 70), text1, fontsize=11, fontname="helv")

    # Page 2: Detailed Findings
    page2 = doc.new_page(width=595, height=842)
    text2 = """DETAILED INSPECTION FINDINGS & FIELD MEASUREMENTS

1. Asset ID: PUMP-104B (Charge Pump 2B)
- Operational status: Running at 2950 RPM
- Vibration level exceeds ISO standard 10816-3 threshold (7.8 mm/s RMS).
- Inboard bearing housing temperature recorded at 68C (nominal ceiling: 55C).
- Audible cavitation signature present at suction flange.

2. Asset ID: TANK-301A (Condensate Storage Vessel)
- Construction: Welded carbon steel, nominal thickness 6.0 mm
- External shell wall thickness measured at 3.1 mm against 4.0 mm nominal minimum.
- Localized pitting corrosion detected on lower shell ring segment 3.
- Foundation settlement within allowable 5 mm limit.

3. Asset ID: PIPE-EX-12 (Exchanger Bypass Spool)
- Flange gasket minor weeping observed at Joint #4; within operational tolerance.
- Ultrasonic thickness survey confirms remaining wall thickness of 5.8 mm (min req: 4.2 mm).
- External protective coating intact with no visible atmospheric corrosion.

INSPECTION CERTIFICATION
I hereby certify that the measurements and visual assessments recorded in this document
accurately reflect the physical condition of the assets at the time of inspection.

Signed: J. Doe, Certified Integrity Inspector
"""
    page2.insert_text((50, 70), text2, fontsize=11, fontname="helv")

    doc.set_metadata({
        "title": "Equipment Integrity Inspection Report",
        "author": "J. Doe",
        "subject": "Mechanical Integrity Inspection",
        "keywords": "inspection, integrity, pump, vessel, piping",
    })

    doc.save(str(path))
    doc.close()
    return path


if not SYNTHETIC_EQUIPMENT_WORKBOOK.exists():
    create_synthetic_equipment_spreadsheet(SYNTHETIC_EQUIPMENT_WORKBOOK)

if not SYNTHETIC_INSPECTION_REPORT_PDF.exists():
    create_synthetic_inspection_report(SYNTHETIC_INSPECTION_REPORT_PDF)
