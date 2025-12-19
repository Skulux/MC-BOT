from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import requests
import websockets
from flask import Flask, jsonify, render_template, request


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["WS_URL"] = os.environ.get("WS_URL", "ws://localhost:8765")
    app.config["OLLAMA_URL"] = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    app.config["OLLAMA_MODEL"] = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
    repo_root = Path(__file__).resolve().parents[1]
    bot_process: subprocess.Popen | None = None
    ollama_process: subprocess.Popen | None = None

    async def _ws_command(payload: dict) -> dict:
        async with websockets.connect(app.config["WS_URL"]) as ws:
            payload = {"id": 1, **payload}
            await ws.send(json.dumps(payload))
            async for msg in ws:
                data = json.loads(msg)
                if data.get("id") == 1:
                    return data
        return {}

    def send_ws_command(payload: dict) -> dict:
        try:
            return asyncio.run(_ws_command(payload))
        except RuntimeError:
            return asyncio.new_event_loop().run_until_complete(_ws_command(payload))

    def get_tool_schema() -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_status",
                    "description": "Get current bot status including position, health, and food.",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_base",
                    "description": "Get the stored home/base position.",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "go_home",
                    "description": "Navigate to the stored home/base position.",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "set_base",
                    "description": "Store the current position as home/base.",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "go_to",
                    "description": "Navigate to a position.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "z": {"type": "number"},
                            "range": {"type": "number", "default": 1}
                        },
                        "required": ["x", "y", "z"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "stop",
                    "description": "Stop navigation and movement.",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_item",
                    "description": "Get or craft an item, including prerequisites.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "count": {"type": "number", "default": 1}
                        },
                        "required": ["name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "chat",
                    "description": "Send a chat message in-game.",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "inventory",
                    "description": "List inventory items.",
                    "parameters": {"type": "object", "properties": {}}
                }
            }
        ]

    def run_tool_call(name: str, args: dict) -> dict:
        if name == "get_status":
            return send_ws_command({"cmd": "status"})
        if name == "get_base":
            return send_ws_command({"cmd": "get_base"})
        if name == "go_home":
            base = send_ws_command({"cmd": "get_base"})
            if base.get("type") == "error":
                return base
            pos = base.get("base") or {}
            if not pos:
                return {"error": "Base not set"}
            return send_ws_command({"cmd": "goto", "pos": pos, "range": 1})
        if name == "set_base":
            return send_ws_command({"cmd": "set_base"})
        if name == "go_to":
            return send_ws_command({
                "cmd": "goto",
                "pos": {"x": args["x"], "y": args["y"], "z": args["z"]},
                "range": args.get("range", 1)
            })
        if name == "stop":
            return send_ws_command({"cmd": "stop"})
        if name == "get_item":
            return send_ws_command({"cmd": "get", "name": args["name"], "count": args.get("count", 1)})
        if name == "chat":
            return send_ws_command({"cmd": "chat", "text": args["text"]})
        if name == "inventory":
            return send_ws_command({"cmd": "inventory"})
        return {"error": f"Unknown tool: {name}"}

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

    @app.get("/api/ai-status")
    def ai_status():
        nonlocal ollama_process
        running = ollama_process is not None and ollama_process.poll() is None
        return jsonify({"running": running})

    @app.post("/api/ai-start")
    def ai_start():
        nonlocal ollama_process
        if ollama_process is not None and ollama_process.poll() is None:
            return jsonify({"running": True, "message": "ollama already running"})
        ollama_process = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        return jsonify({"running": True, "message": "ollama started"})

    @app.post("/api/ai-stop")
    def ai_stop():
        nonlocal ollama_process
        if ollama_process is None or ollama_process.poll() is not None:
            return jsonify({"running": False, "message": "ollama not running"})
        ollama_process.terminate()
        return jsonify({"running": False, "message": "ollama stopped"})

    @app.post("/api/ai-chat")
    def ai_chat():
        payload = request.get_json(force=True) or {}
        prompt = payload.get("message", "").strip()
        if not prompt:
            return jsonify({"error": "Missing message"}), 400

        system = (
            "You are an AI Minecraft assistant. Use tools when needed to control the bot. "
            "Prefer tool calls for actions like movement, getting items, status, or chat."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ]
        tools = get_tool_schema()
        resp = requests.post(
            f"{app.config['OLLAMA_URL']}/api/chat",
            json={"model": app.config["OLLAMA_MODEL"], "messages": messages, "tools": tools}
        )
        resp.raise_for_status()
        data = resp.json()
        message = data.get("message", {})

        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            tool_results = []
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name")
                args = fn.get("arguments") or {}
                tool_results.append({
                    "role": "tool",
                    "name": name,
                    "content": json.dumps(run_tool_call(name, args))
                })
            followup_messages = messages + [message] + tool_results
            followup = requests.post(
                f"{app.config['OLLAMA_URL']}/api/chat",
                json={"model": app.config["OLLAMA_MODEL"], "messages": followup_messages}
            )
            followup.raise_for_status()
            final_message = followup.json().get("message", {})
            return jsonify({"response": final_message.get("content", ""), "tools_used": tool_calls})

        return jsonify({"response": message.get("content", ""), "tools_used": []})

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
