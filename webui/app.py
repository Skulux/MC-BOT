from __future__ import annotations

import os

from flask import Flask, render_template


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["WS_URL"] = os.environ.get("WS_URL", "ws://localhost:8765")

    @app.get("/")
    def index() -> str:
        return render_template("index.html", ws_url=app.config["WS_URL"])

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
