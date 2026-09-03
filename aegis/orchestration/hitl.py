"""HITL (Human-in-the-Loop) approval state machine.

The state machine is owned exclusively by ``ExecutionController``.
The UI submits an approval request; the Controller routes it through this
module.  The UI must never mutate execution state directly.

Valid transitions
-----------------
DRAFT → WAITING_FOR_APPROVAL   (submit)
WAITING_FOR_APPROVAL → APPROVED (approve)
WAITING_FOR_APPROVAL → REJECTED (reject)
APPROVED → FINAL               (finalize)

Any other transition raises ``InvalidHITLTransitionError``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import FrozenSet, Mapping
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class HITLApprovalState(StrEnum):
    """Named stages of the HITL approval lifecycle."""

    DRAFT = "draft"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    FINAL = "final"


# Authoritative transition table: maps (from_state, to_state) -> decision label.
_VALID_TRANSITIONS: Mapping[tuple[HITLApprovalState, HITLApprovalState], str] = {
    (HITLApprovalState.DRAFT, HITLApprovalState.WAITING_FOR_APPROVAL): "submit",
    (HITLApprovalState.WAITING_FOR_APPROVAL, HITLApprovalState.APPROVED): "approve",
    (HITLApprovalState.WAITING_FOR_APPROVAL, HITLApprovalState.REJECTED): "reject",
    (HITLApprovalState.APPROVED, HITLApprovalState.FINAL): "finalize",
}

# Terminal states — no further transitions are legal.
_TERMINAL_STATES: FrozenSet[HITLApprovalState] = frozenset(
    {HITLApprovalState.REJECTED, HITLApprovalState.FINAL}
)


# ---------------------------------------------------------------------------
# Decision record
# ---------------------------------------------------------------------------

class HITLApprovalDecision(BaseModel):
    """An immutable, JSON-serializable record of one HITL state transition.

    Every field required by the governance specification is present.
    The record is frozen so audit consumers receive stable values.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: UUID = Field(default_factory=uuid4)
    user_id: str | None = Field(default=None, min_length=1)
    task_id: UUID
    session_id: UUID
    timestamp: datetime = Field(default_factory=_utc_now)
    previous_state: HITLApprovalState
    new_state: HITLApprovalState
    decision: str = Field(min_length=1)

    @field_validator("timestamp")
    @classmethod
    def _require_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone information")
        return value


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class InvalidHITLTransitionError(ValueError):
    """Raised when a state transition is not permitted by the state machine."""

    def __init__(
        self,
        from_state: HITLApprovalState,
        to_state: HITLApprovalState,
    ) -> None:
        allowed = [str(t) for (f, t) in _VALID_TRANSITIONS if f == from_state]
        super().__init__(
            f"Invalid HITL transition: {from_state!r} -> {to_state!r}. "
            f"Allowed transitions from {from_state!r}: "
            f"{allowed if allowed else ['none (terminal state)']}."
        )
        self.from_state = from_state
        self.to_state = to_state


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class HITLApprovalStateMachine:
    """Deterministic HITL approval state machine owned by ExecutionController.

    Usage
    -----
    The Controller constructs one instance per approval-required task.
    After ``verify_result`` succeeds the Controller calls ``submit()``.
    The Controller's ``record_approval`` method calls ``approve()`` or
    ``reject()``.  After approval, ``finish`` calls ``finalize()``.

    The state machine never calls back into the Controller or UI layer.
    """

    def __init__(
        self,
        task_id: UUID,
        session_id: UUID,
        user_id: str | None = None,
    ) -> None:
        self._state: HITLApprovalState = HITLApprovalState.DRAFT
        self._task_id = task_id
        self._session_id = session_id
        self._default_user_id = user_id
        self._history: list[HITLApprovalDecision] = []

    # ------------------------------------------------------------------
    # Public read-only interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> HITLApprovalState:
        """Current approval state."""
        return self._state

    @property
    def history(self) -> tuple[HITLApprovalDecision, ...]:
        """Ordered, immutable record of every completed transition."""
        return tuple(self._history)

    # ------------------------------------------------------------------
    # Transition methods
    # ------------------------------------------------------------------

    def submit(self, user_id: str | None = None) -> HITLApprovalDecision:
        """Transition DRAFT -> WAITING_FOR_APPROVAL.

        Called by the Controller after ``verify_result`` succeeds.
        """
        return self._transition(HITLApprovalState.WAITING_FOR_APPROVAL, user_id)

    def approve(self, user_id: str | None = None) -> HITLApprovalDecision:
        """Transition WAITING_FOR_APPROVAL -> APPROVED.

        Called by the Controller when the human operator approves.
        """
        return self._transition(HITLApprovalState.APPROVED, user_id)

    def reject(self, user_id: str | None = None) -> HITLApprovalDecision:
        """Transition WAITING_FOR_APPROVAL -> REJECTED.

        Called by the Controller when the human operator rejects.
        """
        return self._transition(HITLApprovalState.REJECTED, user_id)

    def finalize(self, user_id: str | None = None) -> HITLApprovalDecision:
        """Transition APPROVED -> FINAL.

        Called by the Controller when the task successfully finishes after
        approval.
        """
        return self._transition(HITLApprovalState.FINAL, user_id)

    # ------------------------------------------------------------------
    # Internal guarded transition
    # ------------------------------------------------------------------

    def _transition(
        self,
        to_state: HITLApprovalState,
        user_id: str | None,
    ) -> HITLApprovalDecision:
        """Guard, execute, and record one state transition."""

        transition_key = (self._state, to_state)
        if transition_key not in _VALID_TRANSITIONS:
            raise InvalidHITLTransitionError(self._state, to_state)

        decision_label = _VALID_TRANSITIONS[transition_key]
        previous = self._state
        self._state = to_state

        record = HITLApprovalDecision(
            task_id=self._task_id,
            session_id=self._session_id,
            user_id=user_id or self._default_user_id,
            previous_state=previous,
            new_state=to_state,
            decision=decision_label,
        )
        self._history.append(record)
        return record
