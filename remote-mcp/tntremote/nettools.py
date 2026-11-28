"""Bridge to TNT AV Net Tools (package tnt-av-net-tools).

Each call runs in its own short-lived Python process that imports the Net
Tools code and collects what the tool would have shown on screen as text.
That keeps the always-on server small: the Net Tools code (and pygame) only
use memory while a tool is running.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from . import brand as B

NET_LIB = "/usr/share/APPLaunch/lib/tnt-av-net-tools"

# action -> (module, function, [allowed args], seconds before we give up)
ACTIONS = {
    "network_basics": ("basics", "basics", [], 40),
    "ping": ("basics", "ping", ["target", "count", "size"], 90),
    "traceroute": ("basics", "traceroute", ["target"], 120),
    "ip_scan": ("scan", "scan", ["iface"], 180),
    "find_all_devices": ("scan", "find_all", ["iface"], 200),
    "find_av_gear": ("av", "find_av", ["iface"], 200),
    "dante_devices": ("av", "dante", ["iface"], 60),
    "dante_health": ("av", "dante_health", ["iface", "seconds"], 200),
    "ndi_sources": ("av", "ndi", ["iface"], 60),
    "aes67_sap_streams": ("av", "sap", ["iface", "seconds"], 200),
    "ptp_leader": ("av", "ptp_view", ["iface", "seconds"], 200),
    "multicast_igmp": ("av", "multicast", ["iface", "seconds"], 200),
    "mdns_services": ("av", "mdns_all", ["iface"], 60),
    "control_port_check": ("av", "port_check", ["ip", "extra_ports"], 120),
    "dhcp_rogue_check": ("checks", "dhcp", ["iface", "seconds"], 60),
    "ip_conflict_check": ("checks", "conflict", ["iface", "target"], 60),
    "lldp_cdp": ("checks", "lldp", ["iface", "seconds"], 200),
    "link_errors": ("checks", "link", ["iface", "seconds"], 200),
}
BRANDS = ["crestron", "extron", "qsys", "biamp", "shure", "netgear"]


def available() -> bool:
    return os.path.isdir(os.path.join(NET_LIB, "tntnet"))


def _run(module: str, func: str, kwargs: dict, timeout: int) -> str:
    if not available():
        return "TNT AV Net Tools is not installed on this CardputerZero (package tnt-av-net-tools)."
    env = dict(os.environ)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = NET_LIB + ":" + here
    env.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", PYGAME_HIDE_SUPPORT_PROMPT="1")
    env["PATH"] = env.get("PATH", "/usr/bin:/bin") + ":/usr/sbin:/sbin"
    try:
        p = subprocess.run([sys.executable, "-m", __package__ + ".netrun", module, func, json.dumps(kwargs)],
                           cwd=NET_LIB, env=env, capture_output=True, text=True, timeout=timeout + 15)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return out + "\n[stopped: took too long]"
    text = p.stdout.strip()
    if p.returncode != 0 and p.stderr.strip():
        text += "\n--- error ---\n" + p.stderr.strip()[-3000:]
    return text or "(no output)"


def net_tool(args: dict, cfg: dict, ctx) -> str:
    from .tools import ToolError, _cap
    if not cfg.get("allow_net_tools"):
        raise ToolError("Net Tools are switched off on the device (" + B.APP_NAME + " > Permissions).")
    action = str(args.get("action") or "")
    if action not in ACTIONS:
        raise ToolError("action must be one of: " + ", ".join(ACTIONS))
    module, func, allowed, timeout = ACTIONS[action]
    kw = {k: str(args[k]) for k in allowed if args.get(k) not in (None, "")}
    if "seconds" in kw:
        timeout = max(timeout, int(float(kw["seconds"])) + 30)
    ctx.note(f"net: {action}")
    return _cap(_run(module, func, kw, timeout))


def av_device_info(args: dict, cfg: dict, ctx) -> str:
    from .tools import ToolError, _cap
    if not cfg.get("allow_net_tools"):
        raise ToolError("Net Tools are switched off on the device (" + B.APP_NAME + " > Permissions).")
    brand = str(args.get("brand") or "").lower()
    if brand not in BRANDS:
        raise ToolError("brand must be one of: " + ", ".join(BRANDS))
    ip = str(args.get("ip") or "")
    if not ip:
        raise ToolError("ip is required")
    kw = {"ip": ip, "user": str(args.get("user") or ""), "password": str(args.get("password") or "")}
    if args.get("mode"):
        kw["mode"] = str(args["mode"])
    ctx.note(f"info: {brand} {ip}")
    return _cap(_run("devices", brand, kw, 150))
