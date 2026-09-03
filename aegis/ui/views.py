"""Gradio component and layout definitions for the AEGIS application shell.

Defines the login, USER, and ADMIN views with strict separation from business logic.
"""

from __future__ import annotations

from typing import NamedTuple
import gradio as gr


class LoginComponents(NamedTuple):
    container: gr.Column
    username: gr.Textbox
    password: gr.Textbox
    submit_btn: gr.Button
    status_msg: gr.Markdown


class UserViewComponents(NamedTuple):
    container: gr.Row
    # Sidebar
    new_session_btn: gr.Button
    session_list: gr.Radio
    current_user_display: gr.Markdown
    logout_btn: gr.Button
    # Main
    chat_history: gr.Chatbot
    file_upload: gr.File
    task_input: gr.Textbox
    submit_btn: gr.Button
    events_display: gr.Markdown
    result_display: gr.Markdown
    approval_group: gr.Group
    approval_banner: gr.Markdown
    approve_btn: gr.Button
    reject_btn: gr.Button
    approval_result_msg: gr.Markdown


class AdminViewComponents(NamedTuple):
    container: gr.Row
    # Sidebar
    nav_radio: gr.Radio
    current_user_display: gr.Markdown
    logout_btn: gr.Button
    # Main Panes
    dashboard_group: gr.Group
    dashboard_content: gr.Markdown
    users_group: gr.Group
    users_table: gr.Dataframe
    sessions_group: gr.Group
    sessions_table: gr.Dataframe
    audit_group: gr.Group
    audit_table: gr.Dataframe
    network_group: gr.Group
    network_summary: gr.Markdown
    network_table: gr.Dataframe
    models_group: gr.Group
    models_table: gr.Dataframe


def build_login_view() -> LoginComponents:
    """Construct the initial authentication screen."""
    with gr.Column(visible=True, elem_id="login-view") as container:
        gr.Markdown(
            "# AEGIS Sovereign Agentic AI Workbench\n"
            "Confidential on-premise industrial AI assistance. Please sign in."
        )
        with gr.Row():
            with gr.Column(scale=2):
                username = gr.Textbox(
                    label="Username",
                    placeholder="Enter username (e.g. alice, bob, admin)",
                    autofocus=True,
                )
                password = gr.Textbox(
                    label="Password",
                    type="password",
                    placeholder="Enter password",
                )
                submit_btn = gr.Button("Sign In", variant="primary")
                status_msg = gr.Markdown("", visible=True)
            with gr.Column(scale=1):
                gr.Markdown(
                    "### Prototype Credentials\n"
                    "- **alice** / `password123` (USER)\n"
                    "- **bob** / `password123` (USER)\n"
                    "- **admin** / `adminpass` (ADMIN)\n\n"
                    "*No external IAM or cloud service is used.*"
                )

    return LoginComponents(
        container=container,
        username=username,
        password=password,
        submit_btn=submit_btn,
        status_msg=status_msg,
    )


def build_user_view() -> UserViewComponents:
    """Construct the operator/USER role workbench layout."""
    with gr.Row(visible=False, elem_id="user-view") as container:
        # Sidebar
        with gr.Column(scale=1, variant="panel", elem_id="user-sidebar"):
            gr.Markdown("## AEGIS")
            new_session_btn = gr.Button("New Session", variant="primary")
            session_list = gr.Radio(
                label="Sessions",
                choices=[],
                value=None,
                interactive=True,
            )
            current_user_display = gr.Markdown("**Not signed in**")
            logout_btn = gr.Button("Logout", variant="secondary")

        # Main Area
        with gr.Column(scale=4, elem_id="user-main"):
            chat_history = gr.Chatbot(
                label="Task Conversation",
                height=300,
            )

            file_upload = gr.File(
                label="Upload Task Attachment (PDF / Spreadsheet / Image)",
                file_types=[".pdf", ".xlsx", ".csv", ".png", ".jpg", ".jpeg"],
                type="filepath",
            )

            with gr.Row():
                task_input = gr.Textbox(
                    label="Task Request",
                    placeholder="Describe your calculation, inspection review, or multimodal task...",
                    lines=2,
                    scale=4,
                )
                submit_btn = gr.Button("Run Task", variant="primary", scale=1)

            gr.Markdown("### Execution-Event Stream")
            events_display = gr.Markdown(
                "*No events recorded yet. Submit a task to begin governed execution.*",
                elem_id="events-display",
            )

            gr.Markdown("### Deliverable & Result Area")
            result_display = gr.Markdown(
                "*Deliverables and calculation results will appear here.*",
                elem_id="result-display",
            )

            with gr.Group(visible=False) as approval_group:
                approval_banner = gr.Markdown(
                    "### Human-In-The-Loop Approval Required\n"
                    "The task has prepared a deliverable that requires human authorization."
                )
                with gr.Row():
                    approve_btn = gr.Button("Approve Clearance", variant="primary")
                    reject_btn = gr.Button("Reject Clearance", variant="stop")
                approval_result_msg = gr.Markdown("")

    return UserViewComponents(
        container=container,
        new_session_btn=new_session_btn,
        session_list=session_list,
        current_user_display=current_user_display,
        logout_btn=logout_btn,
        chat_history=chat_history,
        file_upload=file_upload,
        task_input=task_input,
        submit_btn=submit_btn,
        events_display=events_display,
        result_display=result_display,
        approval_group=approval_group,
        approval_banner=approval_banner,
        approve_btn=approve_btn,
        reject_btn=reject_btn,
        approval_result_msg=approval_result_msg,
    )


def build_admin_view() -> AdminViewComponents:
    """Construct the ADMIN role governance dashboard layout."""
    with gr.Row(visible=False, elem_id="admin-view") as container:
        # Sidebar
        with gr.Column(scale=1, variant="panel", elem_id="admin-sidebar"):
            gr.Markdown("## AEGIS Admin")
            nav_radio = gr.Radio(
                label="Navigation",
                choices=[
                    "Dashboard",
                    "Users",
                    "Sessions",
                    "Audit",
                    "Network",
                    "Model Health",
                ],
                value="Dashboard",
                interactive=True,
            )
            current_user_display = gr.Markdown("**Admin**")
            logout_btn = gr.Button("Logout", variant="secondary")

        # Main Panes
        with gr.Column(scale=4, elem_id="admin-main"):
            # Dashboard Pane
            with gr.Group(visible=True) as dashboard_group:
                gr.Markdown("### System Overview Dashboard")
                dashboard_content = gr.Markdown("Loading dashboard metrics...")

            # Users Pane
            with gr.Group(visible=False) as users_group:
                gr.Markdown("### Registered Prototype Users")
                users_table = gr.Dataframe(
                    headers=["user_id", "username", "role", "display_name"],
                    datatype=["str", "str", "str", "str"],
                    interactive=False,
                )

            # Sessions Pane
            with gr.Group(visible=False) as sessions_group:
                gr.Markdown("### All Sessions (Cross-User Governance)")
                sessions_table = gr.Dataframe(
                    headers=["session_id", "user_id", "created_at", "status"],
                    datatype=["str", "str", "str", "str"],
                    interactive=False,
                )

            # Audit Pane
            with gr.Group(visible=False) as audit_group:
                gr.Markdown("### Execution Event Audit Log")
                audit_table = gr.Dataframe(
                    headers=[
                        "sequence",
                        "timestamp",
                        "event_type",
                        "status",
                        "component",
                        "summary",
                        "task_id",
                        "user_id",
                    ],
                    datatype=["number", "str", "str", "str", "str", "str", "str", "str"],
                    interactive=False,
                )

            # Network Pane
            with gr.Group(visible=False) as network_group:
                gr.Markdown("### Local & Sandbox Network Activity Monitor")
                network_summary = gr.Markdown("Loading network telemetry...")
                network_table = gr.Dataframe(
                    headers=[
                        "timestamp",
                        "direction",
                        "destination",
                        "destination_port",
                        "protocol",
                        "classification",
                        "status",
                    ],
                    datatype=["str", "str", "str", "str", "str", "str", "str"],
                    interactive=False,
                )

            # Model Health Pane
            with gr.Group(visible=False) as models_group:
                gr.Markdown("### Model Provider Health & Routing Status")
                models_table = gr.Dataframe(
                    headers=[
                        "model_id",
                        "provider",
                        "role",
                        "context_window",
                        "available",
                        "health",
                        "enabled",
                    ],
                    datatype=["str", "str", "str", "number", "bool", "str", "bool"],
                    interactive=False,
                )

    return AdminViewComponents(
        container=container,
        nav_radio=nav_radio,
        current_user_display=current_user_display,
        logout_btn=logout_btn,
        dashboard_group=dashboard_group,
        dashboard_content=dashboard_content,
        users_group=users_group,
        users_table=users_table,
        sessions_group=sessions_group,
        sessions_table=sessions_table,
        audit_group=audit_group,
        audit_table=audit_table,
        network_group=network_group,
        network_summary=network_summary,
        network_table=network_table,
        models_group=models_group,
        models_table=models_table,
    )
