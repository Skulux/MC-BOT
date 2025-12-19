import asyncio
import json
import itertools
import shlex
from typing import Any, Dict, Optional

import websockets

WS_URL = "ws://localhost:8765"
_id = itertools.count(1)

HELP_TEXT = """
Commands:
  help
  status
  chat <message...>

  goto <x> <y> <z> [range]
  lookat <x> <y> <z>
  stop

  dig <x> <y> <z>
  collect <block_name> [count] [radius]
  place <ref_x> <ref_y> <ref_z> <face 0..5> <item_name>

  inv | inventory
  toss <item_name> [count]

  # Simple “walk” (WASD-like). Runs for N seconds then stops automatically.
  walk <forward|back|left|right> <seconds> [jump] [sprint] [sneak]

Notes:
- 'store' (chest deposit/withdraw) is not implemented yet in bot.js.
  If you want it, I can add 'chest_deposit'/'chest_withdraw' commands next.
"""

async def send(ws, cmd: Dict[str, Any]) -> int:
    cmd["id"] = next(_id)
    await ws.send(json.dumps(cmd))
    return cmd["id"]

async def receiver(ws):
    async for msg in ws:
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            print("<< non-json:", msg)
            continue

        if data.get("type") == "event":
            ev = data.get("event")
            if ev == "chat":
                print(f"\n[CHAT] {data.get('username')}: {data.get('message')}")
            elif ev == "pos":
                p = data.get("pos")
                print(f"\n[POS] {p}")
            elif ev == "health":
                print(f"\n[HEALTH] hp={data.get('health')} food={data.get('food')}")
            else:
                print("\n[EVENT]", data)
        else:
            # responses
            t = data.get("type", data.get("message", "resp"))
            if data.get("type") == "error":
                print(f"\n[ERR] {data.get('message')}")
            else:
                print(f"\n[RESP:{t}] {data}")

        # re-print prompt nicely
        print("> ", end="", flush=True)

def _to_float(s: str) -> float:
    return float(s.replace(",", "."))

def _to_int(s: str) -> int:
    return int(float(s.replace(",", ".")))

async def async_input(prompt: str = "") -> str:
    # Async-friendly stdin read
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))

def parse_command(line: str) -> Optional[Dict[str, Any]]:
    line = line.strip()
    if not line:
        return None

    parts = shlex.split(line)
    if not parts:
        return None

    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("help", "?"):
        print(HELP_TEXT)
        return None

    if cmd == "status":
        return {"cmd": "status"}

    if cmd == "chat":
        if not args:
            print("Usage: chat <message...>")
            return None
        return {"cmd": "chat", "text": " ".join(args)}

    if cmd == "goto":
        if len(args) < 3:
            print("Usage: goto <x> <y> <z> [range]")
            return None
        x, y, z = map(_to_float, args[:3])
        r = _to_int(args[3]) if len(args) >= 4 else 1
        return {"cmd": "goto", "pos": {"x": x, "y": y, "z": z}, "range": r}

    if cmd == "lookat":
        if len(args) != 3:
            print("Usage: lookat <x> <y> <z>")
            return None
        x, y, z = map(_to_float, args)
        return {"cmd": "look_at", "pos": {"x": x, "y": y, "z": z}}

    if cmd == "stop":
        return {"cmd": "stop"}

    if cmd == "dig":
        if len(args) != 3:
            print("Usage: dig <x> <y> <z>")
            return None
        x, y, z = map(_to_float, args)
        return {"cmd": "dig", "pos": {"x": x, "y": y, "z": z}}

    if cmd == "collect":
        if len(args) < 1:
            print("Usage: collect <block_name> [count] [radius]")
            return None
        name = args[0]
        count = _to_int(args[1]) if len(args) >= 2 else 1
        radius = _to_int(args[2]) if len(args) >= 3 else 32
        return {"cmd": "collect", "name": name, "count": count, "radius": radius}

    if cmd == "place":
        if len(args) < 5:
            print("Usage: place <ref_x> <ref_y> <ref_z> <face 0..5> <item_name>")
            return None
        rx, ry, rz = map(_to_float, args[:3])
        face = _to_int(args[3])
        item_name = " ".join(args[4:])
        return {
            "cmd": "place",
            "reference": {"x": rx, "y": ry, "z": rz},
            "face": face,
            "itemName": item_name,
        }

    if cmd in ("inv", "inventory"):
        return {"cmd": "inventory"}

    if cmd == "toss":
        if len(args) < 1:
            print("Usage: toss <item_name> [count]")
            return None
        name = args[0]
        count = _to_int(args[1]) if len(args) >= 2 else 1
        return {"cmd": "toss", "name": name, "count": count}

    if cmd == "walk":
        if len(args) < 2:
            print("Usage: walk <forward|back|left|right> <seconds> [jump] [sprint] [sneak]")
            return None
        direction = args[0].lower()
        seconds = float(args[1])
        flags = set(a.lower() for a in args[2:])

        if direction not in ("forward", "back", "left", "right"):
            print("Direction must be one of: forward, back, left, right")
            return None

        state = {direction: True}
        if "jump" in flags:
            state["jump"] = True
        if "sprint" in flags:
            state["sprint"] = True
        if "sneak" in flags:
            state["sneak"] = True

        # Special marker for timed walk (handled in main loop)
        return {"__timed_walk__": True, "seconds": seconds, "state": state}

    print("Unknown command. Type 'help'.")
    return None

async def main():
    print(f"Connecting to {WS_URL} ...")
    async with websockets.connect(WS_URL) as ws:
        recv_task = asyncio.create_task(receiver(ws))

        print("Connected. Type 'help' for commands.")
        print("> ", end="", flush=True)

        try:
            while True:
                line = await async_input("")
                parsed = parse_command(line)
                if not parsed:
                    print("> ", end="", flush=True)
                    continue

                # Timed walk helper
                if parsed.get("__timed_walk__"):
                    state = parsed["state"]
                    seconds = parsed["seconds"]
                    await send(ws, {"cmd": "control", "state": state})
                    await asyncio.sleep(seconds)
                    # stop movement keys (keep other toggles off too)
                    await send(ws, {"cmd": "control", "state": {
                        "forward": False, "back": False, "left": False, "right": False,
                        "jump": False, "sprint": False, "sneak": False
                    }})
                    print("> ", end="", flush=True)
                    continue

                await send(ws, parsed)
                print("> ", end="", flush=True)

        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
        finally:
            recv_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
