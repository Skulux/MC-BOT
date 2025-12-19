from __future__ import annotations

import os
import subprocess
from pathlib import Path

from flask import Flask, jsonify, render_template


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["WS_URL"] = os.environ.get("WS_URL", "ws://localhost:8765")
    repo_root = Path(__file__).resolve().parents[1]
    bot_process: subprocess.Popen | None = None

    @app.get("/")
    def index() -> str:
        return render_template("index.html", ws_url=app.config["WS_URL"])

    @app.get("/api/bot-status")
    def bot_status():
        nonlocal bot_process
        running = bot_process is not None and bot_process.poll() is None
        return jsonify({"running": running})

    @app.post("/api/start-bot")
    def start_bot():
        nonlocal bot_process
        if bot_process is not None and bot_process.poll() is None:
            return jsonify({"running": True, "message": "bot already running"})
        bot_process = subprocess.Popen(
            ["node", "bot.js"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        return jsonify({"running": True, "message": "bot started"})

    @app.post("/api/stop-bot")
    def stop_bot():
        nonlocal bot_process
        if bot_process is None or bot_process.poll() is not None:
            return jsonify({"running": False, "message": "bot not running"})
        bot_process.terminate()
        return jsonify({"running": False, "message": "bot stopped"})

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
