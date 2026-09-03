"""Entry point for launching the AEGIS Gradio application.

Run with:
    python -m aegis.ui
"""

from aegis.ui.app import create_app

if __name__ == "__main__":
    app = create_app()
    app.launch(server_name="127.0.0.1", server_port=7860)
