"""Installed radio packages.

Each radio package (a separate .deb) puts one folder under
/usr/share/APPLaunch/lib/rig-companion-zero/radios/<id>/ with:

  radio.json   name, version, the radios it covers and their features
  driver.py    defines `Driver`, a subclass of rigzero.radio.Radio

The splash screen lists what is installed, and only the chosen driver is
imported. Every driver has the same worker interface, so the screens and keys
stay the same.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ORDER = ("kenwood",)       # listed first; the rest follow by name


def search_paths():
    paths = [Path(__file__).resolve().parent.parent / "radios"]
    extra = os.environ.get("RIG_COMPANION_RADIOS")
    if extra:
        paths = [Path(item) for item in extra.split(os.pathsep)] + paths
    return paths


def installed():
    """Radio packages found on disk, Kenwood first."""
    packages, seen = [], set()
    for root in search_paths():
        if not root.is_dir():
            continue
        for manifest in sorted(root.glob("*/radio.json")):
            try:
                package = json.loads(manifest.read_text())
            except (OSError, ValueError):
                continue
            if package.get("id") in seen or not package.get("radios"):
                continue
            seen.add(package["id"])
            package["folder"] = str(manifest.parent)
            for radio in package["radios"]:
                radio["features"] = set(radio.get("features", ()))
                radio["family"] = package["id"]
                radio["prefixes"] = tuple(radio.get("prefixes", ()))
            packages.append(package)
    packages.sort(key=lambda p: (ORDER.index(p["id"]) if p["id"] in ORDER else len(ORDER), p["name"]))
    return packages


def find(package_id):
    return next((package for package in installed() if package["id"] == package_id), None)


def load(package):
    """Import only this package's driver module and return its Driver class."""
    name = "rig_companion_radio_" + package["id"].replace("-", "_")
    if name in sys.modules:
        return sys.modules[name].Driver
    path = Path(package["folder"]) / package.get("driver", "driver.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module.Driver


def summary(package):
    names = [radio["key"] for radio in package["radios"]]
    return ", ".join(names[:3]) + (" +" + str(len(names) - 3) if len(names) > 3 else "")
