from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

import requests
import websockets
from flask import Flask, jsonify, render_template, request


def create_app() -> Flask:
    app = Flask(__name__)
    repo_root = Path(__file__).resolve().parents[1]
    app.config["WS_URL"] = os.environ.get("WS_URL", "ws://localhost:8765")
    app.config["OLLAMA_URL"] = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    app.config["OLLAMA_MODEL"] = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
    app.config["AI_MEMORY_PATH"] = os.environ.get(
        "AI_MEMORY_PATH", str(repo_root / "webui" / "ai_memory.json")
    )
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

    def parse_ollama_json(resp: requests.Response) -> dict:
        try:
            return resp.json()
        except (ValueError, json.JSONDecodeError, requests.exceptions.JSONDecodeError):
            lines = [line for line in resp.text.splitlines() if line.strip()]
            if not lines:
                raise
            return json.loads(lines[-1])

    def load_ai_memory() -> list[dict]:
        path = Path(app.config["AI_MEMORY_PATH"])
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, json.JSONDecodeError):
            return []

    def save_ai_memory(messages: list[dict]) -> None:
        path = Path(app.config["AI_MEMORY_PATH"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(messages[-50:], indent=2), encoding="utf-8")

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
                    "name": "get_position",
                    "description": "Get the current bot position (XYZ).",
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
                    "name": "dig_block",
                    "description": "Dig a block at a position.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "z": {"type": "number"}
                        },
                        "required": ["x", "y", "z"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "place_block",
                    "description": "Place a block on the given face of a reference block.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ref_x": {"type": "number"},
                            "ref_y": {"type": "number"},
                            "ref_z": {"type": "number"},
                            "face": {"type": "number"},
                            "item_name": {"type": "string"}
                        },
                        "required": ["ref_x", "ref_y", "ref_z", "face", "item_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "use_block",
                    "description": "Use/activate a block (e.g. crafting table or furnace).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "z": {"type": "number"}
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
                    "name": "scan_blocks",
                    "description": "List nearby blocks around the bot.",
                    "parameters": {
                        "type": "object",
                        "properties": {"radius": {"type": "number", "default": 6}}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "find_blocks",
                    "description": "Find nearby blocks by name (e.g. oak_log).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "radius": {"type": "number", "default": 16},
                            "count": {"type": "number", "default": 10}
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
        if name == "get_position":
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
        if name == "dig_block":
            return send_ws_command({"cmd": "dig", "pos": {"x": args["x"], "y": args["y"], "z": args["z"]}})
        if name == "place_block":
            return send_ws_command({
                "cmd": "place",
                "reference": {"x": args["ref_x"], "y": args["ref_y"], "z": args["ref_z"]},
                "face": args["face"],
                "itemName": args["item_name"]
            })
        if name == "use_block":
            return send_ws_command({"cmd": "use", "pos": {"x": args["x"], "y": args["y"], "z": args["z"]}})
        if name == "stop":
            return send_ws_command({"cmd": "stop"})
        if name == "scan_blocks":
            return send_ws_command({"cmd": "scan_blocks", "radius": args.get("radius", 6)})
        if name == "find_blocks":
            return send_ws_command({
                "cmd": "find_blocks",
                "name": args["name"],
                "radius": args.get("radius", 16),
                "count": args.get("count", 10)
            })
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
            "Prefer tool calls for actions like movement, mining, placing, status, or chat. "
            "Use scan_blocks or find_blocks before digging to avoid punching air. "
            "Do not claim actions are completed without tool results that confirm success. "
            "You must call tools for actions instead of suggesting slash commands."
        )
        memory = load_ai_memory()
        messages = [
            {"role": "system", "content": system},
            *memory,
            {"role": "user", "content": prompt}
        ]
        tools = get_tool_schema()
        resp = requests.post(
            f"{app.config['OLLAMA_URL']}/api/chat",
            json={
                "model": app.config["OLLAMA_MODEL"],
                "messages": messages,
                "tools": tools,
                "stream": False
            }
        )
        resp.raise_for_status()
        data = parse_ollama_json(resp)
        message = data.get("message", {})

        tool_calls = message.get("tool_calls") or []
        if not tool_calls and isinstance(message.get("content"), str):
            content = message["content"]
            find_match = re.search(r"/find\s+([a-zA-Z_]+)(?:\s+count\s+(\d+))?", content)
            scan_match = re.search(r"/scan(?:\s+(\d+))?", content)
            dig_match = re.search(r"/dig\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", content)
            if find_match:
                tool_calls = [{
                    "function": {
                        "name": "find_blocks",
                        "arguments": {
                            "name": find_match.group(1),
                            "count": int(find_match.group(2) or 10)
                        }
                    }
                }]
            elif scan_match:
                tool_calls = [{
                    "function": {
                        "name": "scan_blocks",
                        "arguments": {"radius": int(scan_match.group(1) or 6)}
                    }
                }]
            elif dig_match:
                tool_calls = [{
                    "function": {
                        "name": "dig_block",
                        "arguments": {
                            "x": int(dig_match.group(1)),
                            "y": int(dig_match.group(2)),
                            "z": int(dig_match.group(3))
                        }
                    }
                }]
        if tool_calls:
            tool_results = []
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name")
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_results.append({
                    "role": "tool",
                    "name": name,
                    "content": json.dumps(run_tool_call(name, args))
                })
            followup_messages = messages + [message] + tool_results
            followup = requests.post(
                f"{app.config['OLLAMA_URL']}/api/chat",
                json={
                    "model": app.config["OLLAMA_MODEL"],
                    "messages": followup_messages,
                    "stream": False
                }
            )
            followup.raise_for_status()
            final_message = parse_ollama_json(followup).get("message", {})
            new_memory = memory + [
                {"role": "user", "content": prompt},
                message,
                *tool_results,
                {"role": "assistant", "content": final_message.get("content", "")}
            ]
            save_ai_memory(new_memory)
            return jsonify({"response": final_message.get("content", ""), "tools_used": tool_calls})

        new_memory = memory + [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": message.get("content", "")}
        ]
        save_ai_memory(new_memory)
        return jsonify({"response": message.get("content", ""), "tools_used": []})

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
