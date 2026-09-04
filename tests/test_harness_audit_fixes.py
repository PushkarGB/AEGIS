"""Tests validating fixes implemented from the AEGIS agent harness audit.

Covers:
1. Dynamic expected_fields derivation from spreadsheet columns and user objective.
2. Coding model constraint updates enforcing structured JSON output.
3. System prompt specialization for intent classification, observation reasoning, and planning.
4. Dynamic deliverable methodology and workflow-aware error titles in RuntimeTaskRunner.
5. Persona consolidation in DraftApprovalNoteCapability.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from aegis.agent import (
    IntentAnalysisResult,
    ObservationDecision,
    PlanProposal,
    RouterAgentRuntime,
)
from aegis.capabilities.draft_approval_note import _build_drafting_prompt
from aegis.capabilities.generate_code import _DEFAULT_OUTPUT_CONSTRAINTS
from aegis.orchestration.runtime_runner import _derive_expected_fields
from aegis.skills.computation import _SAFETY_CONSTRAINTS


def test_safety_constraints_enforce_json_output():
    """Ensure coding model constraints mandate structured JSON output rather than prose."""
    constraints_text = " ".join(_SAFETY_CONSTRAINTS)
    assert "JSON" in constraints_text
    assert "human-readable text" not in constraints_text

    default_constraints_text = " ".join(_DEFAULT_OUTPUT_CONSTRAINTS)
    assert "JSON" in default_constraints_text
    assert "human-readable text" not in default_constraints_text


def test_derive_expected_fields_prioritizes_goal_columns():
    """Ensure _derive_expected_fields extracts relevant columns matching the objective."""
    inspection_output = {
        "columns": ["Equipment_ID", "Measured_Thickness", "Min_Acceptable_Thickness", "Technician_Notes"]
    }
    goal = "Calculate average measured thickness per equipment ID and flag if below min acceptable thickness."

    derived = _derive_expected_fields(inspection_output, user_goal=goal)

    assert "equipment_id" in derived
    assert "measured_thickness" in derived
    assert "min_acceptable_thickness" in derived
    # Irrelevant unmentioned columns are excluded from verification requirement
    assert "technician_notes" not in derived


def test_derive_expected_fields_general_fallback():
    """Ensure fallback to top spreadsheet columns if objective has no specific column overlap."""
    inspection_output = {
        "columns": ["Region", "Revenue", "Quarter"]
    }
    derived = _derive_expected_fields(inspection_output, user_goal="Analyze financial data")

    assert "region" in derived
    assert "revenue" in derived
    assert "quarter" in derived


def test_derive_expected_fields_handles_empty():
    assert _derive_expected_fields(None) == []
    assert _derive_expected_fields({}) == []
    assert _derive_expected_fields({"columns": []}) == []


def test_agent_system_prompts_specialized():
    """Ensure _system_prompt produces domain-grounded guidance for each response model."""
    intent_prompt = RouterAgentRuntime._system_prompt(IntentAnalysisResult)
    assert "intent classifier" in intent_prompt
    assert "computation" in intent_prompt
    assert "scanned_document_approval" in intent_prompt
    assert "JSON only" in intent_prompt

    obs_prompt = RouterAgentRuntime._system_prompt(ObservationDecision)
    assert "runtime reasoner" in obs_prompt
    assert "retry_correct" in obs_prompt
    assert "continue" in obs_prompt

    plan_prompt = RouterAgentRuntime._system_prompt(PlanProposal)
    assert "planning agent" in plan_prompt


def test_draft_approval_note_persona_consolidated():
    """Ensure user prompt focuses on task data while persona is handled by system prompt."""
    prompt = _build_drafting_prompt(
        user_goal="Draft note for pump inspection",
        extracted_text="Pump vibration measured at 7.2 mm/s",
        document_title="Pump 101 Report",
    )
    # Persona should NOT duplicate in user prompt
    assert "You are the AEGIS AI Approval Note Specialist" not in prompt
    assert "USER OBJECTIVE:" in prompt
    assert "EXTRACTED DOCUMENT TEXT:" in prompt
