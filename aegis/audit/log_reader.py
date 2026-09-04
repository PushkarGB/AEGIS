"""Reader and aggregator for persistent execution event audit logs.

Provides:
- AuditLogReader: Reads persistent JSONL logs and structures them hierarchically
  by user_id -> session_id -> task_id (user request).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AuditLogReader:
    """Reads and indexes JSONL audit events for structured cascading consumption."""

    def __init__(self, log_path: Path | str = Path("data/audit/events.jsonl")) -> None:
        self._log_path = Path(log_path)

    @property
    def log_path(self) -> Path:
        return self._log_path

    def read_all_events(self) -> list[dict[str, Any]]:
        """Read all raw event dicts from the JSONL file in order."""
        if not self._log_path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return events

    def get_grouped_logs(self) -> dict[str, dict[str, dict[str, list[dict[str, Any]]]]]:
        """Group all events by user_id -> session_id -> task_id (request).

        Returns:
            {
                user_id: {
                    session_id: {
                        task_id: [event_dicts...]
                    }
                }
            }
        """
        events = self.read_all_events()
        grouped: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {}

        for ev in events:
            user = ev.get("user_id") or "anonymous"
            session = str(ev.get("session_id") or "no_session")
            task = str(ev.get("task_id") or "no_task")

            if user not in grouped:
                grouped[user] = {}
            if session not in grouped[user]:
                grouped[user][session] = {}
            if task not in grouped[user][session]:
                grouped[user][session][task] = []
            grouped[user][session][task].append(ev)

        return grouped

    def get_users(self) -> list[str]:
        """Return list of distinct users found in the audit trail."""
        grouped = self.get_grouped_logs()
        return sorted(grouped.keys())

    def get_sessions(self, user_id: str) -> list[str]:
        """Return list of session IDs for a specific user, sorted newest first by event timestamp."""
        grouped = self.get_grouped_logs()
        user_sessions = grouped.get(user_id, {})
        if not user_sessions:
            return []

        def _latest_timestamp(sess_id: str) -> str:
            tasks = user_sessions.get(sess_id, {})
            all_timestamps = [
                str(ev.get("timestamp", ""))
                for task_events in tasks.values()
                for ev in task_events
                if ev.get("timestamp")
            ]
            return max(all_timestamps) if all_timestamps else ""

        return sorted(user_sessions.keys(), key=_latest_timestamp, reverse=True)

    def get_tasks(self, user_id: str, session_id: str) -> list[str]:
        """Return list of task/request IDs for a given user and session, sorted newest first by event timestamp."""
        grouped = self.get_grouped_logs()
        tasks = grouped.get(user_id, {}).get(session_id, {})
        if not tasks:
            return []

        def _latest_timestamp(t_id: str) -> str:
            all_timestamps = [
                str(ev.get("timestamp", ""))
                for ev in tasks.get(t_id, [])
                if ev.get("timestamp")
            ]
            return max(all_timestamps) if all_timestamps else ""

        return sorted(tasks.keys(), key=_latest_timestamp, reverse=True)

    def get_request_events(
        self, user_id: str, session_id: str, task_id: str
    ) -> list[dict[str, Any]]:
        """Return all events for a specific user request."""
        grouped = self.get_grouped_logs()
        return grouped.get(user_id, {}).get(session_id, {}).get(task_id, [])

    def get_request_diagnostic_summary(
        self, user_id: str, session_id: str, task_id: str
    ) -> dict[str, Any]:
        """Extract structured diagnostics for a request.

        Includes:
        - raw model responses
        - task state snapshots
        - controller decisions
        - capabilities requested and called
        - complete raw event list
        """
        events = self.get_request_events(user_id, session_id, task_id)

        model_responses: list[dict[str, Any]] = []
        task_states: list[dict[str, Any]] = []
        controller_decisions: list[dict[str, Any]] = []
        capabilities_requested: list[str] = []
        capabilities_called: list[str] = []

        for ev in events:
            metadata = ev.get("metadata") or {}

            # Raw model response and prompt from agent runtime and capabilities
            if ev.get("event_type") == "model_invoked" or "model_raw_response" in metadata:
                model_responses.append(
                    {
                        "event_id": ev.get("event_id"),
                        "timestamp": ev.get("timestamp"),
                        "model_id": ev.get("model_id"),
                        "model_provider_id": ev.get("model_provider_id"),
                        "role": metadata.get("role"),
                        "task_type": metadata.get("task_type"),
                        "prompt": metadata.get("prompt") or metadata.get("model_prompt"),
                        "system_prompt": metadata.get("system_prompt"),
                        "model_raw_response": metadata.get("model_raw_response"),
                    }
                )

            # TaskState snapshot from controller
            if "task_state_snapshot" in metadata:
                task_states.append(
                    {
                        "event_id": ev.get("event_id"),
                        "timestamp": ev.get("timestamp"),
                        "snapshot": metadata["task_state_snapshot"],
                    }
                )

            # Execution controller decision
            if "execution_controller_decision" in metadata:
                controller_decisions.append(
                    {
                        "event_id": ev.get("event_id"),
                        "timestamp": ev.get("timestamp"),
                        "decision": metadata["execution_controller_decision"],
                        "allowed_next_actions": metadata.get("allowed_next_actions", []),
                    }
                )

            # Capabilities requested / called
            if "capability_requested" in metadata:
                cap_req = metadata["capability_requested"]
                if cap_req not in capabilities_requested:
                    capabilities_requested.append(cap_req)
            if "capability_called" in metadata:
                cap_call = metadata["capability_called"]
                if cap_call not in capabilities_called:
                    capabilities_called.append(cap_call)
            elif ev.get("capability_id"):
                cap_id = ev["capability_id"]
                if cap_id not in capabilities_called:
                    capabilities_called.append(cap_id)

        return {
            "user_id": user_id,
            "session_id": session_id,
            "task_id": task_id,
            "total_events": len(events),
            "model_responses": model_responses,
            "task_state_snapshots": task_states,
            "controller_decisions": controller_decisions,
            "capabilities_requested": capabilities_requested,
            "capabilities_called": capabilities_called,
            "events": events,
        }
