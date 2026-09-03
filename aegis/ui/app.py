"""Main Gradio application assembly and event wiring for AEGIS.

Implements role-based view transitions and wires component events strictly
through the UIBackendService facade.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import gradio as gr

from aegis.auth.models import UserIdentity, UserRole
from aegis.orchestration.hitl import HITLApprovalState
from aegis.ui.event_stream import format_progressive_events
from aegis.ui.service import UIBackendService, UIStreamUpdate, UITaskResult
from aegis.ui.views import (
    AdminViewComponents,
    LoginComponents,
    UserViewComponents,
    build_admin_view,
    build_login_view,
    build_user_view,
)


def _format_events_markdown(events: list[Any]) -> str:
    """Format a list of ExecutionEvents into clean Markdown."""
    if not events:
        return "*No execution events recorded.*"

    lines = [
        "| Time | Component | Event | Status | Summary |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for ev in events:
        ts = ev.timestamp.strftime("%H:%M:%S")
        lines.append(
            f"| `{ts}` | `{ev.component}` | `{ev.event_type}` | **{ev.status.upper()}** | {ev.summary} |"
        )
    return "\n".join(lines)


def _format_dashboard_markdown(data: dict[str, Any]) -> str:
    """Format admin dashboard metrics into Markdown cards."""
    return (
        f"#### System Governance Metrics\n\n"
        f"- **Total Users**: {data.get('total_users', 0)}\n"
        f"- **Total Sessions**: {data.get('total_sessions', 0)} "
        f"(*Active*: {data.get('active_sessions', 0)})\n"
        f"- **Total Audit Records**: {data.get('total_audit_events', 0)}\n"
        f"- **Network Observations**: {data.get('network_observations', 0)} "
        f"(*Egress violations*: {data.get('network_egress_violations', 0)})\n"
        f"- **Registered Models**: {data.get('total_models', 0)} "
        f"(*Available*: {data.get('available_models', 0)})\n\n"
        f"**Sovereignty Status**: Air-gapped / Local-only. Zero external egress."
    )


def _format_network_summary(summary: dict[str, Any]) -> str:
    """Format network monitor summary into Markdown."""
    return (
        f"**Observed Traffic**: Total `{summary.get('total_observations', 0)}` | "
        f"Internal `{summary.get('internal_count', 0)}` | "
        f"External `{summary.get('external_count', 0)}` | "
        f"Blocked `{summary.get('blocked_count', 0)}` | "
        f"Unknown `{summary.get('unknown_count', 0)}`\n\n"
        f"**Policy Violations**: `{summary.get('policy_violations', 0)}` (Enforcing strict no-egress policy)"
    )


def handle_approval_decision(
    approved: bool,
    current_state: dict[str, Any],
    backend: UIBackendService,
) -> tuple[dict[str, Any], ...]:
    """Execute approval or rejection transition and prepare UI component updates."""
    token = current_state.get("token")
    session_id = current_state.get("active_session_id")
    task_id = current_state.get("active_task_id")
    if not token or not session_id or not task_id:
        return (
            current_state,
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )

    try:
        res: UITaskResult = backend.record_approval(
            token_str=token,
            session_id=session_id,
            task_id=task_id,
            approved=approved,
        )
    except Exception as exc:
        new_state = dict(current_state)
        new_state["active_task_id"] = None
        return (
            new_state,
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(value=f"**Error**: {exc}", visible=True),
            gr.update(visible=False, interactive=False),
            gr.update(visible=False, interactive=False),
            gr.update(visible=False),
        )

    new_state = dict(current_state)
    messages = list(new_state.get("chat_messages", []))
    decision_label = "Approved clearance" if approved else "Rejected clearance"
    messages.append({"role": "user", "content": f"**Operator Decision**: {decision_label}"})
    messages.append({"role": "assistant", "content": res.result_text})
    new_state["chat_messages"] = messages
    new_state["active_task_id"] = None

    events_md = _format_events_markdown(res.events)
    decision_msg = (
        "### Decision: Approved\nTask clearance has been approved and finalized."
        if approved
        else "### Decision: Rejected\nTask clearance has been rejected by operator."
    )

    return (
        new_state,
        gr.update(value=messages),
        gr.update(value=events_md),
        gr.update(value=res.result_text),
        gr.update(value=decision_msg, visible=True),
        gr.update(visible=False, interactive=False),
        gr.update(visible=False, interactive=False),
        gr.update(visible=False),
    )


def create_app(service: UIBackendService | None = None) -> gr.Blocks:
    """Build and wire the complete AEGIS Gradio application shell."""
    backend = service or UIBackendService()

    with gr.Blocks(title="AEGIS Sovereign Workbench") as demo:
        # Per-browser session state
        state = gr.State(
            lambda: {
                "token": None,
                "user": None,
                "active_session_id": None,
                "active_task_id": None,
                "chat_messages": [],
            }
        )

        login_view = build_login_view()
        user_view = build_user_view()
        admin_view = build_admin_view()

        # ------------------------------------------------------------------
        # 1. Login Handler
        # ------------------------------------------------------------------
        def handle_login(username: str, password: str, current_state: dict[str, Any]):
            success, msg, user, token = backend.login(username.strip(), password)
            if not success or user is None or token is None:
                return (
                    current_state,
                    gr.update(value=msg, visible=True),
                    gr.update(visible=True),   # login
                    gr.update(visible=False),  # user
                    gr.update(visible=False),  # admin
                    gr.update(),               # session_list
                    gr.update(),               # user_display
                    gr.update(),               # admin_display
                    gr.update(),               # dashboard
                )

            new_state = dict(current_state)
            new_state["token"] = token
            new_state["user"] = user

            if user.role == UserRole.ADMIN:
                dash_data = backend.get_admin_dashboard(token)
                dash_md = _format_dashboard_markdown(dash_data)
                return (
                    new_state,
                    gr.update(value="", visible=False),
                    gr.update(visible=False),  # login
                    gr.update(visible=False),  # user
                    gr.update(visible=True),   # admin
                    gr.update(),
                    gr.update(),
                    gr.update(value=f"**{user.display_name}** (ADMIN)"),
                    gr.update(value=dash_md),
                )

            # USER role: ensure an active session exists
            sessions = backend.list_sessions(token)
            if not sessions:
                created = backend.create_session(token)
                sessions = [created]

            session_choices = [f"Session {str(s.session_id)[:8]} ({s.status})" for s in sessions]
            active_id = sessions[0].session_id
            new_state["active_session_id"] = active_id
            new_state["chat_messages"] = []

            return (
                new_state,
                gr.update(value="", visible=False),
                gr.update(visible=False),  # login
                gr.update(visible=True),   # user
                gr.update(visible=False),  # admin
                gr.update(choices=session_choices, value=session_choices[0]),
                gr.update(value=f"**{user.display_name}** (USER)"),
                gr.update(),
                gr.update(),
            )

        login_view.submit_btn.click(
            fn=handle_login,
            inputs=[login_view.username, login_view.password, state],
            outputs=[
                state,
                login_view.status_msg,
                login_view.container,
                user_view.container,
                admin_view.container,
                user_view.session_list,
                user_view.current_user_display,
                admin_view.current_user_display,
                admin_view.dashboard_content,
            ],
        )

        # ------------------------------------------------------------------
        # 2. Logout Handlers
        # ------------------------------------------------------------------
        def handle_logout(current_state: dict[str, Any]):
            token = current_state.get("token")
            if token:
                backend.logout(token)

            empty_state = {
                "token": None,
                "user": None,
                "active_session_id": None,
                "active_task_id": None,
                "chat_messages": [],
            }

            return (
                empty_state,
                gr.update(visible=True),   # login
                gr.update(visible=False),  # user
                gr.update(visible=False),  # admin
                gr.update(value=""),       # username
                gr.update(value=""),       # password
                gr.update(value=""),       # status_msg
                gr.update(value=[]),       # chat
                gr.update(value=""),       # task_input
                gr.update(value=None),     # file_upload
                gr.update(value="*No events recorded yet.*"),
                gr.update(value="*Awaiting execution...*"),
                gr.update(visible=False),  # approval_group
            )

        logout_outputs = [
            state,
            login_view.container,
            user_view.container,
            admin_view.container,
            login_view.username,
            login_view.password,
            login_view.status_msg,
            user_view.chat_history,
            user_view.task_input,
            user_view.file_upload,
            user_view.events_display,
            user_view.result_display,
            user_view.approval_group,
        ]

        user_view.logout_btn.click(
            fn=handle_logout,
            inputs=[state],
            outputs=logout_outputs,
        )

        admin_view.logout_btn.click(
            fn=handle_logout,
            inputs=[state],
            outputs=logout_outputs,
        )

        # ------------------------------------------------------------------
        # 3. New Session Handler
        # ------------------------------------------------------------------
        def handle_new_session(current_state: dict[str, Any]):
            token = current_state.get("token")
            if not token:
                return current_state, gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

            new_sess = backend.create_session(token)
            sessions = backend.list_sessions(token)
            session_choices = [f"Session {str(s.session_id)[:8]} ({s.status})" for s in sessions]

            new_state = dict(current_state)
            new_state["active_session_id"] = new_sess.session_id
            new_state["active_task_id"] = None
            new_state["chat_messages"] = []

            return (
                new_state,
                gr.update(choices=session_choices, value=session_choices[0]),
                gr.update(value=[]),
                gr.update(value="*New session started. Submit a task.*"),
                gr.update(value="*Awaiting execution...*"),
                gr.update(visible=False),
            )

        user_view.new_session_btn.click(
            fn=handle_new_session,
            inputs=[state],
            outputs=[
                state,
                user_view.session_list,
                user_view.chat_history,
                user_view.events_display,
                user_view.result_display,
                user_view.approval_group,
            ],
        )

        # ------------------------------------------------------------------
        # 4. Session Selection Handler
        # ------------------------------------------------------------------
        def handle_session_change(selected_label: str | None, current_state: dict[str, Any]):
            token = current_state.get("token")
            if not token or not selected_label:
                return current_state, gr.update(), gr.update(), gr.update()

            # Find matching session by prefix
            sessions = backend.list_sessions(token)
            prefix = selected_label.split()[1] if len(selected_label.split()) > 1 else ""
            matched = next((s for s in sessions if str(s.session_id).startswith(prefix)), None)

            new_state = dict(current_state)
            if matched:
                new_state["active_session_id"] = matched.session_id
                new_state["active_task_id"] = None
                new_state["chat_messages"] = []

            return (
                new_state,
                gr.update(value=[]),
                gr.update(value=f"*Switched to session {prefix}.*"),
                gr.update(visible=False),
            )

        user_view.session_list.change(
            fn=handle_session_change,
            inputs=[user_view.session_list, state],
            outputs=[
                state,
                user_view.chat_history,
                user_view.events_display,
                user_view.approval_group,
            ],
        )

        # ------------------------------------------------------------------
        # 5. Task Submission Handler (Streaming Generator)
        # ------------------------------------------------------------------
        def handle_submit_task(
            prompt: str,
            file_obj: str | None,
            current_state: dict[str, Any],
        ):
            token = current_state.get("token")
            session_id = current_state.get("active_session_id")
            if not token or not session_id or not prompt.strip():
                return (
                    current_state,
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )

            # Use streaming submission for progressive event display
            new_state = dict(current_state)
            final_result: UITaskResult | None = None

            for update in backend.submit_task_streaming(
                token_str=token,
                session_id=session_id,
                prompt=prompt.strip(),
                attachment_path=file_obj,
            ):
                if update.is_final and update.result is not None:
                    final_result = update.result
                    break

                # Yield progressive event updates
                yield (
                    current_state,
                    gr.update(),   # chat_history — no update yet
                    gr.update(),   # task_input — no clear yet
                    gr.update(value=update.events_markdown),
                    gr.update(),   # result_display — no update yet
                    gr.update(),   # approval_group — no update yet
                    gr.update(),   # approve_btn — no update yet
                    gr.update(),   # reject_btn — no update yet
                    gr.update(),   # approval_banner — no update yet
                    gr.update(),   # approval_result_msg — no update yet
                )

            # Final yield with complete result
            if final_result is None:
                # Fallback to synchronous submit if streaming produced no final
                final_result = backend.submit_task(
                    token_str=token,
                    session_id=session_id,
                    prompt=prompt.strip(),
                    attachment_path=file_obj,
                )

            new_state["active_task_id"] = final_result.task_id

            # Update chat messages
            messages = list(new_state.get("chat_messages", []))
            user_msg = prompt.strip()
            if file_obj:
                user_msg += f"\n\n*[Attachment: {file_obj}]*"
            messages.append({"role": "user", "content": user_msg})
            messages.append({"role": "assistant", "content": final_result.result_text})
            new_state["chat_messages"] = messages

            events_md = format_progressive_events(final_result.events)
            is_awaiting_approval = (final_result.hitl_state == HITLApprovalState.WAITING_FOR_APPROVAL)

            yield (
                new_state,
                gr.update(value=messages),
                gr.update(value=""),  # Clear task input
                gr.update(value=events_md),
                gr.update(value=final_result.result_text),
                gr.update(visible=is_awaiting_approval),
                gr.update(visible=is_awaiting_approval, interactive=is_awaiting_approval),
                gr.update(visible=is_awaiting_approval, interactive=is_awaiting_approval),
                gr.update(visible=is_awaiting_approval),
                gr.update(value="", visible=False),
            )

        user_view.submit_btn.click(
            fn=handle_submit_task,
            inputs=[user_view.task_input, user_view.file_upload, state],
            outputs=[
                state,
                user_view.chat_history,
                user_view.task_input,
                user_view.events_display,
                user_view.result_display,
                user_view.approval_group,
                user_view.approve_btn,
                user_view.reject_btn,
                user_view.approval_banner,
                user_view.approval_result_msg,
            ],
        )

        # ------------------------------------------------------------------
        # 6. HITL Approval & Rejection Handlers
        # ------------------------------------------------------------------
        approval_outputs = [
            state,
            user_view.chat_history,
            user_view.events_display,
            user_view.result_display,
            user_view.approval_result_msg,
            user_view.approve_btn,
            user_view.reject_btn,
            user_view.approval_banner,
        ]

        user_view.approve_btn.click(
            fn=lambda s: handle_approval_decision(True, s, backend),
            inputs=[state],
            outputs=approval_outputs,
        )

        user_view.reject_btn.click(
            fn=lambda s: handle_approval_decision(False, s, backend),
            inputs=[state],
            outputs=approval_outputs,
        )

        # ------------------------------------------------------------------
        # 7. Admin Navigation Handlers
        # ------------------------------------------------------------------
        def handle_admin_nav(tab: str, current_state: dict[str, Any]):
            token = current_state.get("token")
            if not token:
                return (
                    gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
                    gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
                )

            # Determine visibility of each pane
            vis_dash = (tab == "Dashboard")
            vis_users = (tab == "Users")
            vis_sess = (tab == "Sessions")
            vis_audit = (tab == "Audit")
            vis_net = (tab == "Network")
            vis_models = (tab == "Model Health")

            dash_md = gr.update()
            users_df = gr.update()
            sess_df = gr.update()
            audit_df = gr.update()
            net_sum = gr.update()
            net_df = gr.update()
            models_df = gr.update()

            if vis_dash:
                data = backend.get_admin_dashboard(token)
                dash_md = gr.update(value=_format_dashboard_markdown(data))
            elif vis_users:
                users_data = backend.get_admin_users(token)
                rows = [[u["user_id"], u["username"], u["role"], u["display_name"]] for u in users_data]
                users_df = gr.update(value=rows)
            elif vis_sess:
                sess_data = backend.get_admin_sessions(token)
                rows = [[s["session_id"], s["user_id"], s["created_at"], s["status"]] for s in sess_data]
                sess_df = gr.update(value=rows)
            elif vis_audit:
                logs = backend.get_admin_audit_logs(token, limit=100)
                rows = [
                    [l["sequence"], l["timestamp"], l["event_type"], l["status"], l["component"], l["summary"], l["task_id"], l["user_id"]]
                    for l in logs
                ]
                audit_df = gr.update(value=rows)
            elif vis_net:
                net_data = backend.get_admin_network(token)
                net_sum = gr.update(value=_format_network_summary(net_data["summary"]))
                rows = [
                    [o["timestamp"], o["direction"], o["destination"], o["destination_port"], o["protocol"], o["classification"], o["status"]]
                    for o in net_data["observations"]
                ]
                net_df = gr.update(value=rows)
            elif vis_models:
                models_data = backend.get_admin_model_health(token)
                rows = [
                    [m["model_id"], m["provider"], m["role"], m["context_window"], m["available"], m["health"], m["enabled"]]
                    for m in models_data
                ]
                models_df = gr.update(value=rows)

            return (
                gr.update(visible=vis_dash),
                gr.update(visible=vis_users),
                gr.update(visible=vis_sess),
                gr.update(visible=vis_audit),
                gr.update(visible=vis_net),
                gr.update(visible=vis_models),
                dash_md,
                users_df,
                sess_df,
                audit_df,
                net_sum,
                net_df,
                models_df,
            )

        admin_view.nav_radio.change(
            fn=handle_admin_nav,
            inputs=[admin_view.nav_radio, state],
            outputs=[
                admin_view.dashboard_group,
                admin_view.users_group,
                admin_view.sessions_group,
                admin_view.audit_group,
                admin_view.network_group,
                admin_view.models_group,
                admin_view.dashboard_content,
                admin_view.users_table,
                admin_view.sessions_table,
                admin_view.audit_table,
                admin_view.network_summary,
                admin_view.network_table,
                admin_view.models_table,
            ],
        )

    return demo
