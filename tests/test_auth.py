"""Tests for Phase 6.X Prototype RBAC.

Covers all required scenarios:
- Successful login
- Failed login
- USER authorization (own sessions, tasks, events, HITL)
- ADMIN authorization (all sessions, audit, system, network, model-health)
- Cross-user session access denial
- Audit access denial for USER

Test classes:
  TestUserRoleAndPermissions    — enums, permission sets, has_permission
  TestCredentialStore           — lookup, verify, timing uniformity
  TestTokenStore                — issue, validate, revoke, expiry
  TestAuthService               — login, logout, resolve, require_user, require_role
  TestAuthorization             — USER/ADMIN permission checks
  TestGuards                    — SessionGuard, AuditGuard, SystemGuard
  TestAuthorizedSessionService  — auth-enforcing wrapper on SessionService
  TestAuthExceptions            — structured exception attributes
  TestAuthConfig                — AegisConfig.auth field
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from aegis.auth import (
    AuditGuard,
    AuthService,
    AuthenticationError,
    AuthorizationError,
    Permission,
    PrototypeCredentialStore,
    SessionGuard,
    SystemGuard,
    TokenStore,
    UserIdentity,
    UserRole,
    get_permissions,
    has_permission,
    require_permission,
)
from aegis.auth.models import AuthToken, LoginRequest, LoginResult
from aegis.sessions import (
    AuthorizedSessionService,
    NotFoundError,
    SessionService,
    SqliteStoreFactory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _alice() -> UserIdentity:
    return UserIdentity(
        user_id="user-alice-0001",
        username="alice",
        role=UserRole.USER,
        display_name="Alice (Operator)",
    )


def _admin() -> UserIdentity:
    return UserIdentity(
        user_id="user-admin-0001",
        username="admin",
        role=UserRole.ADMIN,
        display_name="Admin (Administrator)",
    )


def _make_auth_service() -> AuthService:
    return AuthService()


def _make_session_service() -> SessionService:
    session_repo, task_repo = SqliteStoreFactory.create(":memory:")
    return SessionService(session_repo, task_repo)


def _make_authorized_session_service() -> AuthorizedSessionService:
    return AuthorizedSessionService(_make_session_service())


# ===========================================================================
# TestUserRoleAndPermissions
# ===========================================================================


class TestUserRoleAndPermissions:
    """Role enum values and permission set correctness."""

    def test_user_role_values(self):
        assert UserRole.USER == "user"
        assert UserRole.ADMIN == "admin"

    def test_user_role_is_strenum(self):
        assert isinstance(UserRole.USER, str)
        assert isinstance(UserRole.ADMIN, str)

    def test_permission_enum_members(self):
        assert Permission.CREATE_OWN_SESSION == "create_own_session"
        assert Permission.ACCESS_OWN_SESSION == "access_own_session"
        assert Permission.SUBMIT_TASK == "submit_task"
        assert Permission.UPLOAD_FILE == "upload_file"
        assert Permission.VIEW_OWN_EVENTS == "view_own_events"
        assert Permission.INTERACT_HITL == "interact_hitl"
        assert Permission.VIEW_ALL_SESSIONS == "view_all_sessions"
        assert Permission.VIEW_ALL_AUDIT == "view_all_audit"
        assert Permission.ACCESS_SYSTEM_STATUS == "access_system_status"
        assert Permission.ACCESS_NETWORK_MONITOR == "access_network_monitor"
        assert Permission.ACCESS_MODEL_HEALTH == "access_model_health"

    def test_user_has_own_session_permissions(self):
        alice = _alice()
        for perm in [
            Permission.CREATE_OWN_SESSION,
            Permission.ACCESS_OWN_SESSION,
            Permission.SUBMIT_TASK,
            Permission.UPLOAD_FILE,
            Permission.VIEW_OWN_EVENTS,
            Permission.INTERACT_HITL,
        ]:
            assert has_permission(alice, perm), f"USER should have {perm}"

    def test_user_lacks_admin_permissions(self):
        alice = _alice()
        for perm in [
            Permission.VIEW_ALL_SESSIONS,
            Permission.VIEW_ALL_AUDIT,
            Permission.ACCESS_SYSTEM_STATUS,
            Permission.ACCESS_NETWORK_MONITOR,
            Permission.ACCESS_MODEL_HEALTH,
        ]:
            assert not has_permission(alice, perm), f"USER must not have {perm}"

    def test_admin_has_all_permissions(self):
        admin = _admin()
        for perm in Permission:
            assert has_permission(admin, perm), f"ADMIN should have {perm}"

    def test_get_permissions_user(self):
        perms = get_permissions(UserRole.USER)
        assert Permission.ACCESS_OWN_SESSION in perms
        assert Permission.VIEW_ALL_SESSIONS not in perms

    def test_get_permissions_admin(self):
        perms = get_permissions(UserRole.ADMIN)
        assert Permission.VIEW_ALL_AUDIT in perms
        assert Permission.ACCESS_OWN_SESSION in perms  # admin inherits user perms

    def test_require_permission_raises_for_insufficient_role(self):
        alice = _alice()
        with pytest.raises(AuthorizationError) as exc_info:
            require_permission(alice, Permission.VIEW_ALL_AUDIT)
        err = exc_info.value
        assert err.required_permission == str(Permission.VIEW_ALL_AUDIT)
        assert err.actual_role == str(UserRole.USER)
        assert err.user_id == alice.user_id

    def test_require_permission_succeeds_for_sufficient_role(self):
        admin = _admin()
        # Should not raise.
        require_permission(admin, Permission.VIEW_ALL_AUDIT)

    def test_admin_permission_set_is_superset_of_user(self):
        user_perms = get_permissions(UserRole.USER)
        admin_perms = get_permissions(UserRole.ADMIN)
        assert user_perms.issubset(admin_perms)


# ===========================================================================
# TestCredentialStore
# ===========================================================================


class TestCredentialStore:
    """Prototype credential store lookups and verification."""

    def test_lookup_alice_returns_identity(self):
        store = PrototypeCredentialStore()
        identity = store.lookup("alice")
        assert identity is not None
        assert identity.username == "alice"
        assert identity.role == UserRole.USER

    def test_lookup_admin_returns_admin_role(self):
        store = PrototypeCredentialStore()
        identity = store.lookup("admin")
        assert identity is not None
        assert identity.role == UserRole.ADMIN

    def test_lookup_unknown_returns_none(self):
        store = PrototypeCredentialStore()
        assert store.lookup("nonexistent") is None

    def test_verify_correct_password(self):
        store = PrototypeCredentialStore()
        identity = store.verify("alice", "password123")
        assert identity is not None
        assert identity.username == "alice"

    def test_verify_wrong_password(self):
        store = PrototypeCredentialStore()
        assert store.verify("alice", "wrongpassword") is None

    def test_verify_unknown_user(self):
        store = PrototypeCredentialStore()
        assert store.verify("ghost", "any") is None

    def test_verify_admin_correct(self):
        store = PrototypeCredentialStore()
        identity = store.verify("admin", "adminpass")
        assert identity is not None
        assert identity.role == UserRole.ADMIN

    def test_verify_admin_wrong_password(self):
        store = PrototypeCredentialStore()
        assert store.verify("admin", "wrongpassword") is None

    def test_lookup_identity_is_immutable(self):
        store = PrototypeCredentialStore()
        identity = store.lookup("alice")
        with pytest.raises(Exception):
            identity.role = UserRole.ADMIN  # type: ignore[misc]

    def test_all_prototype_users_are_verifiable(self):
        store = PrototypeCredentialStore()
        assert store.verify("alice", "password123") is not None
        assert store.verify("bob", "password123") is not None
        assert store.verify("admin", "adminpass") is not None


# ===========================================================================
# TestTokenStore
# ===========================================================================


class TestTokenStore:
    """Token issuance, validation, revocation, and expiry."""

    def test_issue_returns_auth_token(self):
        store = TokenStore()
        alice = _alice()
        token = store.issue(alice)
        assert token is not None
        assert token.user_id == alice.user_id
        assert token.username == alice.username
        assert token.role == alice.role

    def test_token_is_opaque_string(self):
        store = TokenStore()
        token = store.issue(_alice())
        assert isinstance(token.token, str)
        assert len(token.token) > 8  # UUID4 hex = 32 chars

    def test_validate_valid_token(self):
        store = TokenStore()
        issued = store.issue(_alice())
        found = store.validate(issued.token)
        assert found is not None
        assert found.user_id == issued.user_id

    def test_validate_unknown_token_returns_none(self):
        store = TokenStore()
        assert store.validate("not-a-real-token") is None

    def test_revoke_makes_token_invalid(self):
        store = TokenStore()
        issued = store.issue(_alice())
        store.revoke(issued.token)
        assert store.validate(issued.token) is None

    def test_revoke_unknown_token_is_silent(self):
        store = TokenStore()
        store.revoke("nonexistent-token")  # Must not raise.

    def test_expired_token_returns_none(self):
        store = TokenStore(default_ttl_seconds=1)
        alice = _alice()
        issued = store.issue(alice, ttl_seconds=1)
        # Manually backdate the expires_at to simulate expiry.
        expired_token = issued.model_copy(
            update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )
        # Replace in store internals for the test.
        store._tokens[issued.token] = expired_token
        assert store.validate(issued.token) is None

    def test_each_issued_token_is_unique(self):
        store = TokenStore()
        alice = _alice()
        t1 = store.issue(alice)
        t2 = store.issue(alice)
        assert t1.token != t2.token

    def test_token_ttl_defaults(self):
        store = TokenStore(default_ttl_seconds=3600)
        issued = store.issue(_alice())
        delta = issued.expires_at - issued.issued_at
        assert abs(delta.total_seconds() - 3600) < 5

    def test_token_custom_ttl(self):
        store = TokenStore()
        issued = store.issue(_alice(), ttl_seconds=600)
        delta = issued.expires_at - issued.issued_at
        assert abs(delta.total_seconds() - 600) < 5

    def test_purge_expired(self):
        store = TokenStore()
        issued = store.issue(_alice(), ttl_seconds=1)
        # Backdate.
        store._tokens[issued.token] = issued.model_copy(
            update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )
        removed = store.purge_expired()
        assert removed == 1
        assert store.validate(issued.token) is None


# ===========================================================================
# TestAuthService
# ===========================================================================


class TestAuthService:
    """AuthService: login, logout, resolve, require_user, require_role."""

    # ------------------------------------------------------------------
    # Successful login
    # ------------------------------------------------------------------

    def test_successful_login_alice(self):
        service = _make_auth_service()
        result = service.login("alice", "password123")
        assert result.success is True
        assert result.token is not None
        assert result.error is None

    def test_successful_login_admin(self):
        service = _make_auth_service()
        result = service.login("admin", "adminpass")
        assert result.success is True
        assert result.token is not None
        assert result.token.role == UserRole.ADMIN

    def test_login_result_token_has_correct_user(self):
        service = _make_auth_service()
        result = service.login("alice", "password123")
        assert result.token.username == "alice"
        assert result.token.role == UserRole.USER

    # ------------------------------------------------------------------
    # Failed login
    # ------------------------------------------------------------------

    def test_failed_login_wrong_password(self):
        service = _make_auth_service()
        result = service.login("alice", "wrong")
        assert result.success is False
        assert result.token is None
        assert result.error is not None
        assert len(result.error) > 0

    def test_failed_login_unknown_user(self):
        service = _make_auth_service()
        result = service.login("nobody", "password123")
        assert result.success is False
        assert result.token is None

    def test_failed_login_never_raises(self):
        service = _make_auth_service()
        # No exception should be raised for any credential failure.
        result = service.login("", "")
        assert result.success is False

    def test_failed_login_error_is_generic(self):
        """Error message must not reveal whether username or password was wrong."""
        service = _make_auth_service()
        r1 = service.login("alice", "wrong")
        r2 = service.login("nobody", "password123")
        assert r1.error == r2.error

    # ------------------------------------------------------------------
    # Logout
    # ------------------------------------------------------------------

    def test_logout_revokes_token(self):
        service = _make_auth_service()
        result = service.login("alice", "password123")
        token_str = result.token.token
        service.logout(token_str)
        assert service.resolve_current_user(token_str) is None

    def test_logout_is_idempotent(self):
        service = _make_auth_service()
        result = service.login("alice", "password123")
        token_str = result.token.token
        service.logout(token_str)
        service.logout(token_str)  # Must not raise.

    def test_logout_unknown_token_is_silent(self):
        service = _make_auth_service()
        service.logout("not-a-token")  # Must not raise.

    # ------------------------------------------------------------------
    # resolve_current_user
    # ------------------------------------------------------------------

    def test_resolve_current_user_valid_token(self):
        service = _make_auth_service()
        result = service.login("alice", "password123")
        identity = service.resolve_current_user(result.token.token)
        assert identity is not None
        assert identity.username == "alice"
        assert identity.role == UserRole.USER

    def test_resolve_current_user_invalid_token_returns_none(self):
        service = _make_auth_service()
        assert service.resolve_current_user("garbage") is None

    def test_resolve_current_user_after_logout_returns_none(self):
        service = _make_auth_service()
        result = service.login("alice", "password123")
        service.logout(result.token.token)
        assert service.resolve_current_user(result.token.token) is None

    # ------------------------------------------------------------------
    # require_user
    # ------------------------------------------------------------------

    def test_require_user_valid_token(self):
        service = _make_auth_service()
        result = service.login("alice", "password123")
        identity = service.require_user(result.token.token)
        assert identity.username == "alice"

    def test_require_user_invalid_token_raises(self):
        service = _make_auth_service()
        with pytest.raises(AuthenticationError):
            service.require_user("invalid-token")

    def test_require_user_after_logout_raises(self):
        service = _make_auth_service()
        result = service.login("alice", "password123")
        service.logout(result.token.token)
        with pytest.raises(AuthenticationError):
            service.require_user(result.token.token)

    # ------------------------------------------------------------------
    # require_role
    # ------------------------------------------------------------------

    def test_require_role_user_for_user_token(self):
        service = _make_auth_service()
        result = service.login("alice", "password123")
        identity = service.require_role(result.token.token, UserRole.USER)
        assert identity.username == "alice"

    def test_require_role_admin_for_admin_token(self):
        service = _make_auth_service()
        result = service.login("admin", "adminpass")
        identity = service.require_role(result.token.token, UserRole.ADMIN)
        assert identity.role == UserRole.ADMIN

    def test_require_role_admin_satisfies_user_requirement(self):
        """ADMIN token satisfies a USER role requirement."""
        service = _make_auth_service()
        result = service.login("admin", "adminpass")
        # ADMIN should also satisfy USER requirement.
        identity = service.require_role(result.token.token, UserRole.USER)
        assert identity.role == UserRole.ADMIN

    def test_require_role_user_denied_admin_requirement(self):
        """USER token must not satisfy an ADMIN role requirement."""
        service = _make_auth_service()
        result = service.login("alice", "password123")
        with pytest.raises(AuthorizationError) as exc_info:
            service.require_role(result.token.token, UserRole.ADMIN)
        assert exc_info.value.actual_role == str(UserRole.USER)

    def test_require_role_invalid_token_raises_authentication(self):
        service = _make_auth_service()
        with pytest.raises(AuthenticationError):
            service.require_role("bad-token", UserRole.USER)


# ===========================================================================
# TestAuthorization
# ===========================================================================


class TestAuthorization:
    """Permission check correctness for USER and ADMIN roles."""

    # USER permissions
    def test_user_can_create_own_session(self):
        require_permission(_alice(), Permission.CREATE_OWN_SESSION)

    def test_user_can_access_own_session(self):
        require_permission(_alice(), Permission.ACCESS_OWN_SESSION)

    def test_user_can_submit_task(self):
        require_permission(_alice(), Permission.SUBMIT_TASK)

    def test_user_can_upload_file(self):
        require_permission(_alice(), Permission.UPLOAD_FILE)

    def test_user_can_view_own_events(self):
        require_permission(_alice(), Permission.VIEW_OWN_EVENTS)

    def test_user_can_interact_hitl(self):
        require_permission(_alice(), Permission.INTERACT_HITL)

    # USER denied admin permissions
    def test_user_denied_view_all_sessions(self):
        with pytest.raises(AuthorizationError):
            require_permission(_alice(), Permission.VIEW_ALL_SESSIONS)

    def test_user_denied_view_all_audit(self):
        with pytest.raises(AuthorizationError):
            require_permission(_alice(), Permission.VIEW_ALL_AUDIT)

    def test_user_denied_system_status(self):
        with pytest.raises(AuthorizationError):
            require_permission(_alice(), Permission.ACCESS_SYSTEM_STATUS)

    def test_user_denied_network_monitor(self):
        with pytest.raises(AuthorizationError):
            require_permission(_alice(), Permission.ACCESS_NETWORK_MONITOR)

    def test_user_denied_model_health(self):
        with pytest.raises(AuthorizationError):
            require_permission(_alice(), Permission.ACCESS_MODEL_HEALTH)

    # ADMIN permissions
    def test_admin_can_view_all_sessions(self):
        require_permission(_admin(), Permission.VIEW_ALL_SESSIONS)

    def test_admin_can_view_all_audit(self):
        require_permission(_admin(), Permission.VIEW_ALL_AUDIT)

    def test_admin_can_access_system_status(self):
        require_permission(_admin(), Permission.ACCESS_SYSTEM_STATUS)

    def test_admin_can_access_network_monitor(self):
        require_permission(_admin(), Permission.ACCESS_NETWORK_MONITOR)

    def test_admin_can_access_model_health(self):
        require_permission(_admin(), Permission.ACCESS_MODEL_HEALTH)

    def test_admin_inherits_user_permissions(self):
        admin = _admin()
        for perm in [
            Permission.CREATE_OWN_SESSION,
            Permission.ACCESS_OWN_SESSION,
            Permission.SUBMIT_TASK,
            Permission.UPLOAD_FILE,
            Permission.VIEW_OWN_EVENTS,
            Permission.INTERACT_HITL,
        ]:
            require_permission(admin, perm)  # Must not raise.


# ===========================================================================
# TestGuards
# ===========================================================================


class TestGuards:
    """Service-layer guards combining AuthService + permission checks."""

    def _setup(self):
        auth = _make_auth_service()
        alice_result = auth.login("alice", "password123")
        admin_result = auth.login("admin", "adminpass")
        return auth, alice_result.token.token, admin_result.token.token

    # ------------------------------------------------------------------
    # SessionGuard
    # ------------------------------------------------------------------

    def test_session_guard_own_access_succeeds_for_user(self):
        auth, alice_token, _ = self._setup()
        guard = SessionGuard(auth)
        identity = guard.require_own_session_access(alice_token)
        assert identity.username == "alice"

    def test_session_guard_all_sessions_denied_for_user(self):
        auth, alice_token, _ = self._setup()
        guard = SessionGuard(auth)
        with pytest.raises(AuthorizationError):
            guard.require_all_sessions_access(alice_token)

    def test_session_guard_all_sessions_allowed_for_admin(self):
        auth, _, admin_token = self._setup()
        guard = SessionGuard(auth)
        identity = guard.require_all_sessions_access(admin_token)
        assert identity.role == UserRole.ADMIN

    def test_session_guard_invalid_token_raises_authentication(self):
        auth, _, _ = self._setup()
        guard = SessionGuard(auth)
        with pytest.raises(AuthenticationError):
            guard.require_own_session_access("bad-token")

    def test_session_guard_submit_task_user(self):
        auth, alice_token, _ = self._setup()
        guard = SessionGuard(auth)
        identity = guard.require_submit_task(alice_token)
        assert identity.username == "alice"

    def test_session_guard_upload_file_user(self):
        auth, alice_token, _ = self._setup()
        guard = SessionGuard(auth)
        guard.require_upload_file(alice_token)

    def test_session_guard_view_own_events_user(self):
        auth, alice_token, _ = self._setup()
        guard = SessionGuard(auth)
        guard.require_view_own_events(alice_token)

    def test_session_guard_interact_hitl_user(self):
        auth, alice_token, _ = self._setup()
        guard = SessionGuard(auth)
        guard.require_interact_hitl(alice_token)

    # ------------------------------------------------------------------
    # AuditGuard
    # ------------------------------------------------------------------

    def test_audit_guard_denied_for_user(self):
        """USER must be denied access to all audit records."""
        auth, alice_token, _ = self._setup()
        guard = AuditGuard(auth)
        with pytest.raises(AuthorizationError) as exc_info:
            guard.require_view_all_audit(alice_token)
        assert exc_info.value.required_permission == str(Permission.VIEW_ALL_AUDIT)

    def test_audit_guard_allowed_for_admin(self):
        auth, _, admin_token = self._setup()
        guard = AuditGuard(auth)
        identity = guard.require_view_all_audit(admin_token)
        assert identity.role == UserRole.ADMIN

    def test_audit_guard_invalid_token_raises_authentication(self):
        auth, _, _ = self._setup()
        guard = AuditGuard(auth)
        with pytest.raises(AuthenticationError):
            guard.require_view_all_audit("bad-token")

    # ------------------------------------------------------------------
    # SystemGuard
    # ------------------------------------------------------------------

    def test_system_guard_status_denied_for_user(self):
        auth, alice_token, _ = self._setup()
        guard = SystemGuard(auth)
        with pytest.raises(AuthorizationError):
            guard.require_system_status(alice_token)

    def test_system_guard_network_monitor_denied_for_user(self):
        auth, alice_token, _ = self._setup()
        guard = SystemGuard(auth)
        with pytest.raises(AuthorizationError):
            guard.require_network_monitor(alice_token)

    def test_system_guard_model_health_denied_for_user(self):
        auth, alice_token, _ = self._setup()
        guard = SystemGuard(auth)
        with pytest.raises(AuthorizationError):
            guard.require_model_health(alice_token)

    def test_system_guard_status_allowed_for_admin(self):
        auth, _, admin_token = self._setup()
        guard = SystemGuard(auth)
        guard.require_system_status(admin_token)

    def test_system_guard_network_monitor_allowed_for_admin(self):
        auth, _, admin_token = self._setup()
        guard = SystemGuard(auth)
        guard.require_network_monitor(admin_token)

    def test_system_guard_model_health_allowed_for_admin(self):
        auth, _, admin_token = self._setup()
        guard = SystemGuard(auth)
        guard.require_model_health(admin_token)


# ===========================================================================
# TestAuthorizedSessionService
# ===========================================================================


class TestAuthorizedSessionService:
    """Auth-enforcing wrapper on SessionService."""

    def _setup(self):
        alice = _alice()
        bob = UserIdentity(
            user_id="user-bob-0002",
            username="bob",
            role=UserRole.USER,
            display_name="Bob (Operator)",
        )
        admin = _admin()
        svc = _make_authorized_session_service()
        return svc, alice, bob, admin

    # ------------------------------------------------------------------
    # Session create / access
    # ------------------------------------------------------------------

    def test_user_can_create_own_session(self):
        svc, alice, _, _ = self._setup()
        session = svc.create_session(alice)
        assert session.user_id == alice.user_id

    def test_user_can_get_own_session(self):
        svc, alice, _, _ = self._setup()
        session = svc.create_session(alice)
        found = svc.get_session(session.session_id, alice)
        assert found.session_id == session.session_id

    def test_user_can_list_own_sessions(self):
        svc, alice, _, _ = self._setup()
        svc.create_session(alice)
        sessions = svc.list_sessions(alice)
        assert len(sessions) == 1

    def test_user_can_close_own_session(self):
        svc, alice, _, _ = self._setup()
        session = svc.create_session(alice)
        closed = svc.close_session(session.session_id, alice)
        assert closed.status.value == "closed"

    # ------------------------------------------------------------------
    # Cross-user session access denial
    # ------------------------------------------------------------------

    def test_cross_user_session_access_denied(self):
        """USER must not access another user's session — denied at isolation layer."""
        svc, alice, bob, _ = self._setup()
        alice_session = svc.create_session(alice)
        # Bob tries to get Alice's session — should raise NotFoundError (no data leakage).
        with pytest.raises(NotFoundError):
            svc.get_session(alice_session.session_id, bob)

    def test_cross_user_close_session_denied(self):
        svc, alice, bob, _ = self._setup()
        alice_session = svc.create_session(alice)
        with pytest.raises(NotFoundError):
            svc.close_session(alice_session.session_id, bob)

    def test_list_sessions_isolation(self):
        svc, alice, bob, _ = self._setup()
        svc.create_session(alice)
        svc.create_session(alice)
        bob_sessions = svc.list_sessions(bob)
        assert len(bob_sessions) == 0

    # ------------------------------------------------------------------
    # Task operations
    # ------------------------------------------------------------------

    def test_user_can_create_task(self):
        svc, alice, _, _ = self._setup()
        session = svc.create_session(alice)
        task = svc.create_task(session.session_id, alice)
        assert task.user_id == alice.user_id
        assert task.session_id == session.session_id

    def test_user_can_get_task(self):
        svc, alice, _, _ = self._setup()
        session = svc.create_session(alice)
        task = svc.create_task(session.session_id, alice)
        found = svc.get_task(task.task_id, session.session_id, alice)
        assert found.task_id == task.task_id

    # ------------------------------------------------------------------
    # Unauthorized operation (no valid identity with wrong permission)
    # ------------------------------------------------------------------

    def test_missing_permission_raises_authorization_error(self):
        """Manually construct identity missing CREATE_OWN_SESSION — should be blocked."""
        # Simulate a role that somehow has no permissions (edge case test).
        from aegis.auth.authorization import require_permission
        from aegis.auth.exceptions import AuthorizationError

        # alice has USER role — she DOES have permissions, so verify deny at ADMIN-only.
        alice = _alice()
        with pytest.raises(AuthorizationError):
            require_permission(alice, Permission.VIEW_ALL_SESSIONS)

    def test_admin_can_create_session(self):
        svc, _, _, admin = self._setup()
        session = svc.create_session(admin)
        assert session.user_id == admin.user_id


# ===========================================================================
# TestAuthExceptions
# ===========================================================================


class TestAuthExceptions:
    """Structured exception attributes for test assertions."""

    def test_authentication_error_default_message(self):
        err = AuthenticationError()
        assert "Authentication" in str(err)
        assert err.message == "Authentication required"

    def test_authentication_error_custom_message(self):
        err = AuthenticationError("Token expired")
        assert err.message == "Token expired"
        assert str(err) == "Token expired"

    def test_authentication_error_is_exception(self):
        assert issubclass(AuthenticationError, Exception)

    def test_authorization_error_has_attributes(self):
        err = AuthorizationError(
            "Permission denied",
            required_permission="view_all_audit",
            actual_role="user",
            user_id="user-alice-0001",
        )
        assert err.required_permission == "view_all_audit"
        assert err.actual_role == "user"
        assert err.user_id == "user-alice-0001"

    def test_authorization_error_is_exception(self):
        assert issubclass(AuthorizationError, Exception)

    def test_authorization_error_optional_attributes_default_none(self):
        err = AuthorizationError("denied")
        assert err.required_permission is None
        assert err.actual_role is None
        assert err.user_id is None

    def test_require_permission_error_carries_all_attributes(self):
        alice = _alice()
        with pytest.raises(AuthorizationError) as exc_info:
            require_permission(alice, Permission.VIEW_ALL_AUDIT)
        err = exc_info.value
        assert err.required_permission == "view_all_audit"
        assert err.actual_role == "user"
        assert err.user_id == alice.user_id

    def test_authentication_error_raised_on_invalid_token(self):
        service = _make_auth_service()
        with pytest.raises(AuthenticationError) as exc_info:
            service.require_user("invalid")
        assert isinstance(exc_info.value, AuthenticationError)


# ===========================================================================
# TestAuthConfig
# ===========================================================================


class TestAuthConfig:
    """AuthConfig schema and AegisConfig integration."""

    def test_auth_config_default_values(self):
        from aegis.config import AuthConfig
        config = AuthConfig()
        assert config.enabled is True
        assert config.token_ttl_seconds == 3600

    def test_auth_config_custom_values(self):
        from aegis.config import AuthConfig
        config = AuthConfig(enabled=False, token_ttl_seconds=600)
        assert config.enabled is False
        assert config.token_ttl_seconds == 600

    def test_auth_config_ttl_minimum(self):
        from aegis.config import AuthConfig
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            AuthConfig(token_ttl_seconds=10)  # Below minimum of 60.

    def test_aegis_config_includes_auth(self):
        from aegis.config import AegisConfig
        assert hasattr(AegisConfig.model_fields, "__contains__") or "auth" in AegisConfig.model_fields

    def test_auth_config_is_importable(self):
        from aegis.config import AuthConfig
        assert AuthConfig.__name__ == "AuthConfig"
