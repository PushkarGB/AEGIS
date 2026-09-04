"""Focused tests for AEGIS deliverable artifact download mechanism.

Validates:
- Artifact creation and abstraction preservation
- Artifact registration via ArtifactStore and UIBackendService
- Correct task, session, and user association
- Authorized download for owning user and admin
- Unauthorized cross-user access rejection (RBAC & ownership)
- Graceful handling of missing/expired artifacts (unknown ID & missing disk file)
- End-to-end deliverable download via Gradio UI handlers for .xlsx and .docx
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from aegis.artifacts import ArtifactRecord, ArtifactStore, infer_artifact_media_type
from aegis.auth.exceptions import AuthenticationError, AuthorizationError
from aegis.auth.models import UserRole
from aegis.orchestration.workflows import WorkflowName
from aegis.schemas import Artifact, FinalStatus
from aegis.ui.service import UIBackendService, UITaskResult
from aegis.ui.app import create_app, handle_approval_decision


@pytest.fixture
def tmp_deliverable(tmp_path: Path) -> Path:
    """Create a temporary deliverable file on disk."""
    file_path = tmp_path / "sample_deliverable.xlsx"
    file_path.write_bytes(b"PK\x03\x04synthetic_excel_bytes")
    return file_path


@pytest.fixture
def backend() -> UIBackendService:
    """In-memory UIBackendService with fresh store."""
    return UIBackendService(db_path=":memory:")


def test_artifact_creation(tmp_deliverable: Path):
    """Preserve existing Artifact abstraction and construct storage record."""
    art = Artifact(
        name="inspection_summary.xlsx",
        media_type=infer_artifact_media_type(tmp_deliverable),
        location=str(tmp_deliverable),
        description="Industrial calculation deliverable",
    )

    assert art.artifact_id is not None
    assert art.name == "inspection_summary.xlsx"
    assert art.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert art.location == str(tmp_deliverable)

    store = ArtifactStore()
    task_id = uuid4()
    session_id = uuid4()
    user_id = "alice"

    record = store.register(
        task_id=task_id,
        session_id=session_id,
        user_id=user_id,
        artifact=art,
    )

    assert isinstance(record, ArtifactRecord)
    assert record.artifact_id == art.artifact_id
    assert record.task_id == task_id
    assert record.session_id == session_id
    assert record.user_id == user_id
    assert record.name == art.name
    assert record.location == str(tmp_deliverable)


def test_artifact_registration(backend: UIBackendService, tmp_deliverable: Path):
    """Artifact registration through UIBackendService registers into store."""
    success, _, _, token = backend.login("alice", "password123")
    assert success and token is not None

    session = backend.create_session(token)
    task_id = uuid4()

    art = Artifact(
        name="test_artifact.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        location=str(tmp_deliverable),
    )

    record = backend.register_artifact(
        task_id=task_id,
        session_id=session.session_id,
        user_id="alice",
        artifact=art,
    )

    assert record.artifact_id == art.artifact_id
    fetched = backend.get_artifact(token, art.artifact_id)
    assert fetched.artifact_id == art.artifact_id
    assert fetched.name == "test_artifact.xlsx"


def test_correct_task_session_association(backend: UIBackendService, tmp_deliverable: Path):
    """Artifacts are strictly associated with their session, task, and user."""
    _, _, _, token_alice = backend.login("alice", "password123")
    session_1 = backend.create_session(token_alice)
    session_2 = backend.create_session(token_alice)

    task_1 = uuid4()
    task_2 = uuid4()

    art_1 = Artifact(
        name="task1_deliverable.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        location=str(tmp_deliverable),
    )
    art_2 = Artifact(
        name="task2_deliverable.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        location=str(tmp_deliverable),
    )

    backend.register_artifact(task_1, session_1.session_id, "alice", art_1)
    backend.register_artifact(task_2, session_2.session_id, "alice", art_2)

    task1_records = backend.list_task_artifacts(token_alice, task_1)
    assert len(task1_records) == 1
    assert task1_records[0].artifact_id == art_1.artifact_id
    assert task1_records[0].session_id == session_1.session_id
    assert task1_records[0].user_id == "alice"

    task2_records = backend.list_task_artifacts(token_alice, task_2)
    assert len(task2_records) == 1
    assert task2_records[0].artifact_id == art_2.artifact_id
    assert task2_records[0].session_id == session_2.session_id
    assert task2_records[0].user_id == "alice"


def test_authorized_download(backend: UIBackendService, tmp_deliverable: Path):
    """Owning user and admin can download an artifact without exposing arbitrary paths."""
    _, _, _, token_alice = backend.login("alice", "password123")
    _, _, _, token_admin = backend.login("admin", "adminpass")

    session = backend.create_session(token_alice)
    task_id = uuid4()

    art = Artifact(
        name="equipment_readings.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        location=str(tmp_deliverable),
    )
    record = backend.register_artifact(task_id, session.session_id, "alice", art)

    # 1. Owner download
    dl_path, dl_name = backend.get_artifact_for_download(token_alice, record.artifact_id)
    assert dl_path == str(tmp_deliverable)
    assert dl_name == "equipment_readings.xlsx"

    # 2. Admin download (governance role)
    admin_path, admin_name = backend.get_artifact_for_download(token_admin, record.artifact_id)
    assert admin_path == str(tmp_deliverable)
    assert admin_name == "equipment_readings.xlsx"


def test_unauthorized_access(backend: UIBackendService, tmp_deliverable: Path):
    """A user cannot access or download another user's artifact."""
    _, _, _, token_alice = backend.login("alice", "password123")
    _, _, _, token_bob = backend.login("bob", "password123")

    session = backend.create_session(token_alice)
    task_id = uuid4()

    art = Artifact(
        name="confidential_audit.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        location=str(tmp_deliverable),
    )
    record = backend.register_artifact(task_id, session.session_id, "alice", art)

    # Bob attempts to get Alice's artifact metadata
    with pytest.raises(AuthorizationError) as exc_info:
        backend.get_artifact(token_bob, record.artifact_id)
    assert "not authorized to access artifact" in str(exc_info.value)

    # Bob attempts to download Alice's artifact
    with pytest.raises(AuthorizationError) as exc_info:
        backend.get_artifact_for_download(token_bob, record.artifact_id)
    assert "not authorized to access artifact" in str(exc_info.value)

    # Unauthenticated / invalid token
    with pytest.raises(AuthenticationError):
        backend.get_artifact_for_download("invalid_token_xyz", record.artifact_id)


def test_missing_or_expired_artifact(backend: UIBackendService, tmp_path: Path):
    """Graceful handling of unknown artifact IDs and deleted/missing disk files."""
    _, _, _, token_alice = backend.login("alice", "password123")
    session = backend.create_session(token_alice)
    task_id = uuid4()

    # 1. Unknown artifact ID
    random_id = uuid4()
    with pytest.raises(FileNotFoundError) as exc_info:
        backend.get_artifact_for_download(token_alice, random_id)
    assert f"Artifact '{random_id}' not found." in str(exc_info.value)

    # 2. Registered artifact whose disk file has been removed or expired
    ephemeral_file = tmp_path / "deleted_report.docx"
    ephemeral_file.write_bytes(b"PK\x03\x04temporary_bytes")

    art = Artifact(
        name="deleted_report.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        location=str(ephemeral_file),
    )
    record = backend.register_artifact(task_id, session.session_id, "alice", art)

    # Delete the physical file
    ephemeral_file.unlink()

    # Attempt download -> FileNotFoundError, not crash
    with pytest.raises(FileNotFoundError) as exc_info:
        backend.get_artifact_for_download(token_alice, record.artifact_id)
    assert "missing on disk" in str(exc_info.value)


def test_computation_task_delivers_downloadable_xlsx(backend: UIBackendService):
    """End-to-end task submission produces registered, downloadable .xlsx deliverable."""
    _, _, _, token_alice = backend.login("alice", "password123")
    session = backend.create_session(token_alice)

    # Submit computation task
    result: UITaskResult = backend.submit_task(
        token_str=token_alice,
        session_id=session.session_id,
        prompt="Calculate average measured thickness from spreadsheet readings",
    )

    assert result.final_status == FinalStatus.COMPLETED
    assert len(result.artifact_paths) > 0
    assert len(result.artifact_ids) > 0

    # Ensure deliverable is .xlsx
    first_path = result.artifact_paths[0]
    assert first_path.endswith(".xlsx")

    # Ensure downloadable via backend
    dl_path, dl_name = backend.get_artifact_for_download(token_alice, result.artifact_ids[0])
    assert Path(dl_path).exists()
    assert Path(dl_path).resolve() == Path(first_path).resolve()
    assert dl_name.endswith(".xlsx")


def test_document_drafting_approval_delivers_downloadable_docx(backend: UIBackendService):
    """Approval note workflow produces downloadable .docx upon operator approval."""
    _, _, _, token_alice = backend.login("alice", "password123")
    session = backend.create_session(token_alice)

    # Submit approval note task (triggers HITL pause)
    result: UITaskResult = backend.submit_task(
        token_str=token_alice,
        session_id=session.session_id,
        prompt="Review scanned inspection report and draft approval note clearance",
    )

    assert len(result.artifact_paths) > 0
    assert result.artifact_paths[0].endswith(".docx")
    assert len(result.artifact_ids) > 0

    # Operator approves clearance
    approved_res = backend.record_approval(
        token_str=token_alice,
        session_id=session.session_id,
        task_id=result.task_id,
        approved=True,
    )

    assert approved_res.final_status == FinalStatus.COMPLETED
    assert len(approved_res.artifact_ids) > 0

    dl_path, dl_name = backend.get_artifact_for_download(token_alice, approved_res.artifact_ids[0])
    assert Path(dl_path).exists()
    assert dl_name.endswith(".docx")


def test_gradio_ui_download_component_wiring(backend: UIBackendService):
    """Verify handle_approval_decision and submit yield download component update."""
    _, _, _, token_alice = backend.login("alice", "password123")
    session = backend.create_session(token_alice)

    # Submit task to get active task ID
    result = backend.submit_task(
        token_str=token_alice,
        session_id=session.session_id,
        prompt="Draft approval note review",
    )

    state = {
        "token": token_alice,
        "active_session_id": session.session_id,
        "active_task_id": result.task_id,
        "chat_messages": [],
    }

    # Execute operator approval
    res_tuple = handle_approval_decision(approved=True, current_state=state, backend=backend, include_download=True)
    # The last element of res_tuple is the download component update
    download_update = res_tuple[-1]
    assert download_update is not None
    # Check that update makes the component visible and contains file path
    visible = download_update.get("visible") if isinstance(download_update, dict) else getattr(download_update, "visible", None)
    value = download_update.get("value") if isinstance(download_update, dict) else getattr(download_update, "value", None)
    assert visible is True
    assert value is not None
    assert str(value).endswith(".docx")
