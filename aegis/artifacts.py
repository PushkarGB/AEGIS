"""Artifact registry and access control for AEGIS deliverables.

Preserves the existing Artifact abstraction and binds every registered artifact
to its task_id, session_id, and user_id. Controlled access ensures no arbitrary
filesystem paths are exposed directly to untrusted clients.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from aegis.schemas import Artifact


def infer_artifact_media_type(path: str | Path) -> str:
    """Infer MIME/media type from file extension for deliverables."""
    ext = Path(path).suffix.lower()
    if ext in {".xlsx", ".xls"}:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext in {".docx"}:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if ext in {".csv"}:
        return "text/csv"
    if ext in {".pdf"}:
        return "application/pdf"
    if ext in {".png"}:
        return "image/png"
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "application/octet-stream"


@dataclass(frozen=True)
class ArtifactRecord:
    """Storage metadata record binding an Artifact to its execution context."""

    artifact_id: UUID
    task_id: UUID
    session_id: UUID
    user_id: str
    name: str
    media_type: str
    location: str
    description: str | None
    created_at: datetime


class ArtifactStore:
    """Thread-safe registry for task artifacts with session/user association."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._artifacts: dict[UUID, ArtifactRecord] = {}

    def register(
        self,
        task_id: UUID,
        session_id: UUID,
        user_id: str,
        artifact: Artifact,
    ) -> ArtifactRecord:
        """Register an artifact associated with task, session, and user."""
        record = ArtifactRecord(
            artifact_id=artifact.artifact_id,
            task_id=task_id,
            session_id=session_id,
            user_id=user_id,
            name=artifact.name,
            media_type=artifact.media_type,
            location=artifact.location,
            description=artifact.description,
            created_at=artifact.created_at,
        )
        with self._lock:
            self._artifacts[record.artifact_id] = record
        return record

    def get(self, artifact_id: UUID) -> ArtifactRecord | None:
        """Retrieve artifact record by ID."""
        with self._lock:
            return self._artifacts.get(artifact_id)

    def list_for_task(self, task_id: UUID) -> list[ArtifactRecord]:
        """List all artifact records associated with a task."""
        with self._lock:
            return [rec for rec in self._artifacts.values() if rec.task_id == task_id]

    def list_for_session(self, session_id: UUID) -> list[ArtifactRecord]:
        """List all artifact records associated with a session."""
        with self._lock:
            return [rec for rec in self._artifacts.values() if rec.session_id == session_id]
