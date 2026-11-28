"""MCP (Model Context Protocol) over streamable HTTP: JSON-RPC 2.0 messages
POSTed to /mcp, answered with plain JSON (no streaming needed)."""
from __future__ import annotations

import json
import time
import traceback

from . import __version__, brand, config as C, nettools, statusview, tools as T

PROTOCOLS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

DEV = getattr(brand, "DEVELOPER", ("Developed by TheTechNomad", "www.TheTechNomad.com"))
INSTRUCTIONS = (
    f"You are connected to {getattr(brand, 'OWNER', 'the owner' + chr(39) + 's')} M5Stack CardputerZero (Raspberry Pi CM0, Debian 13 arm64, "
    f"352 MB RAM, 320x170 LCD, keyboard) through {brand.APP_NAME}. Commands run as user 'pi'. "
    "Set sudo=true on run_command/start_background to run as root (apt install, systemctl ...); "
    "that only works while the owner has Admin (sudo) switched on in the device menu, otherwise ask them to turn it on. "
    "Memory is tight: avoid heavy builds, prefer apt packages or pure Python. "
    "Use start_background for anything longer than a couple of minutes. "
    "Launcher apps live in /usr/share/APPLaunch/lib/<package>; source checkouts usually in ~/. "
    f"net_tool and av_device_info use {getattr(brand, 'NET_TOOLS', 'the Net Tools app')} for AV network checks (when installed). "
    "device_status gives a full status report; screenshot_lcd shows what is on the built-in screen. "
    "Every call is logged on the device. "
    f"About {brand.APP_NAME}: " + "; ".join(DEV) + "."
)


def _s(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


STR = {"type": "string"}
INT = {"type": "integer"}
BOOL = {"type": "boolean"}

TOOLS = [
    ("device_status", "Show the CardputerZero's status: health (temperature, uptime, RAM, disk, load), RAM breakdown, network and Wi-Fi, tunnel and public address, permission switches, top programs by memory and installed apps, with an overall verdict. Apps that support MCP Apps show it as a visual status card.",
     _s({}), T.device_status, True),
    ("run_command", "Run a bash command on the CardputerZero as user pi and wait for it (default 120 s, max 900 s). Returns exit code, stdout and stderr.",
     _s({"command": dict(STR, description="bash command line"), "cwd": dict(STR, description="working folder (default home)"),
         "timeout_s": dict(INT, description="seconds to wait, 1-900"), "stdin": dict(STR, description="text to send to the command's input"),
         "sudo": dict(BOOL, description="run as root (needs Admin switched on at the device)")},
        ["command"]), T.run_command, False),
    ("start_background", "Start a long-running command (server, build, capture) in the background. Returns a job id; read its output with read_background.",
     _s({"command": STR, "cwd": STR, "wait_s": dict(INT, description="seconds to wait before returning first output (default 1)"),
         "sudo": dict(BOOL, description="run as root (needs Admin switched on at the device)")},
        ["command"]), T.start_background, False),
    ("read_background", "Read the latest output of a background job.",
     _s({"job_id": INT, "tail_lines": dict(INT, description="last N lines (default 100)")}, ["job_id"]), T.read_background, True),
    ("stop_background", "Stop a background job.", _s({"job_id": INT}, ["job_id"]), T.stop_background, False),
    ("list_background", "List background jobs and whether they are still running.", _s({}), T.list_background, True),
    ("read_file", "Read a text file (by lines) or any file as base64.",
     _s({"path": STR, "start_line": dict(INT, description="first line, 1-based"), "max_lines": dict(INT, description="default 2000"),
         "base64": dict(BOOL, description="return raw bytes as base64"), "offset_bytes": dict(INT, description="with base64: start offset")},
        ["path"]), T.read_file, True),
    ("write_file", "Create or overwrite a file (folders are created). Set append to add to the end, base64 for binary, executable to chmod +x.",
     _s({"path": STR, "content": STR, "append": BOOL, "base64": BOOL, "executable": BOOL}, ["path", "content"]), T.write_file, False),
    ("edit_file", "Replace exact text in a file (old_text must be unique unless replace_all).",
     _s({"path": STR, "old_text": STR, "new_text": STR, "replace_all": BOOL}, ["path", "old_text", "new_text"]), T.edit_file, False),
    ("list_dir", "List a folder (depth 0-4) with sizes and dates.",
     _s({"path": STR, "depth": INT, "show_hidden": BOOL}), T.list_dir, True),
    ("move_path", "Move/rename (or copy with copy=true) a file or folder.",
     _s({"source": STR, "destination": STR, "copy": BOOL}, ["source", "destination"]), T.move_path, False),
    ("delete_path", "Delete a file, or a folder with recursive=true.",
     _s({"path": STR, "recursive": BOOL}, ["path"]), T.delete_path, False),
    ("screenshot_lcd", "Take a screenshot of the CardputerZero's built-in 320x170 screen.", _s({}), T.screenshot, True),
    ("net_tool", "Run a " + getattr(brand, "NET_TOOLS", "Net Tools") + " check on the network the CardputerZero is plugged into and return the results as text. "
                 "iface is e.g. eth0 or wlan0 (blank = best interface); seconds = listen time for passive checks.",
     _s({"action": {"type": "string", "enum": list(nettools.ACTIONS)}, "iface": STR, "target": dict(STR, description="host for ping/traceroute/conflict"),
         "ip": dict(STR, description="device IP for control_port_check"), "count": STR, "size": STR, "seconds": STR,
         "extra_ports": dict(STR, description="extra ports, comma separated")}, ["action"]), nettools.net_tool, True),
    ("av_device_info", "Log in to an AV device (Crestron, Extron, Q-SYS, Biamp, Shure, Netgear AV switch) and pull its info, the same as " + getattr(brand, "NET_TOOLS", "Net Tools") + "' device info. "
                       "Credentials are used once and not stored. Crestron: VER, IPCONFIG, PROGCOMMENTS, uptime, IPTABLE (never ERRLOG).",
     _s({"brand": {"type": "string", "enum": nettools.BRANDS}, "ip": STR, "user": STR, "password": STR,
         "mode": dict(STR, description="optional: SSH / Telnet / Console etc.")}, ["brand", "ip"]), nettools.av_device_info, True),
]
BY_NAME = {t[0]: t for t in TOOLS}


def tool_list() -> list[dict]:
    out = []
    for name, desc, schema, _fn, ro in TOOLS:
        t = {"name": name, "description": desc, "inputSchema": schema}
        if name == "device_status":   # visual status card in apps that show MCP Apps views
            t["_meta"] = {"ui": {"resourceUri": statusview.URI}, "openai/outputTemplate": statusview.URI,
                          "openai/widgetAccessible": False}
        out.append(t)
        t.update({"annotations": {"readOnlyHint": ro, "destructiveHint": name in ("delete_path", "run_command", "write_file", "move_path", "edit_file"),
                                    "openWorldHint": name in ("net_tool", "av_device_info", "run_command")}})
    return out


class Ctx:
    """Per-call context: lets tools post a one-line note to the device screen."""

    def __init__(self, server, client: str):
        self.server, self.client = server, client

    def note(self, text: str) -> None:
        self.server.activity(text)


def audit(client: str, name: str, args: dict, ok: bool, ms: int) -> None:
    safe = {k: ("***" if k in ("password", "content", "stdin", "new_text", "old_text") else (str(v)[:200]))
            for k, v in (args or {}).items()}
    line = json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "client": client, "tool": name,
                       "args": safe, "ok": ok, "ms": ms})
    try:
        C.ensure_dirs()
        with open(C.AUDIT_LOG, "a") as f:
            f.write(line + "\n")
        import os
        if os.path.getsize(C.AUDIT_LOG) > 2_000_000:
            os.replace(C.AUDIT_LOG, C.AUDIT_LOG + ".1")
    except OSError:
        pass


def handle(msg, server, client: str) -> dict | None:
    """One JSON-RPC message -> response dict (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _err(None, -32600, "invalid request")
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:   # notification (initialized, cancelled ...)
        return None
    try:
        if method == "initialize":
            want = params.get("protocolVersion")
            ver = want if want in PROTOCOLS else PROTOCOLS[0]
            return _ok(mid, {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}, "resources": {},
                                                                    "extensions": {"io.modelcontextprotocol/ui": {}}},
                             "serverInfo": {"name": brand.CMD, "title": brand.APP_NAME + " (CardputerZero)", "version": __version__,
                                            "description": getattr(brand, "TAGLINE", "") + ". " + DEV[0] + ".",
                                            "websiteUrl": "https://www.TheTechNomad.com"},
                             "instructions": INSTRUCTIONS})
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": tool_list()})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            t = BY_NAME.get(name)
            if not t:
                return _err(mid, -32602, f"unknown tool {name}")
            cfg = C.load()
            server.activity(f"{name}", client)
            t0 = time.monotonic()
            try:
                res = t[3](args, cfg, Ctx(server, client))
                ok = True
            except T.ToolError as e:
                res, ok = str(e), False
            except Exception as e:
                res, ok = f"{type(e).__name__}: {e}", False
                traceback.print_exc()
            audit(client, name, args, ok, int((time.monotonic() - t0) * 1000))
            if isinstance(res, T.Result):
                return _ok(mid, {"content": [{"type": "text", "text": res.text}],
                                 "structuredContent": res.data, "isError": False})
            content = [res] if isinstance(res, dict) else [{"type": "text", "text": str(res)}]
            return _ok(mid, {"content": content, "isError": not ok})
        if method == "resources/list":
            return _ok(mid, {"resources": [{"uri": statusview.URI, "name": "device-status-view",
                                            "title": "Device status view", "mimeType": statusview.MIME}]})
        if method == "resources/read":
            if params.get("uri") != statusview.URI:
                return _err(mid, -32002, "resource not found")
            return _ok(mid, {"contents": [{"uri": statusview.URI, "mimeType": statusview.MIME, "text": statusview.HTML,
                                           "_meta": {"ui": {"csp": {"connectDomains": [], "resourceDomains": []},
                                                            "prefersBorder": True}}}]})
        if method == "prompts/list":
            return _ok(mid, {"prompts": []})
        if method == "resources/templates/list":
            return _ok(mid, {"resourceTemplates": []})
        return _err(mid, -32601, f"method not found: {method}")
    except Exception as e:
        traceback.print_exc()
        return _err(mid, -32603, f"internal error: {e}")


def _ok(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}
