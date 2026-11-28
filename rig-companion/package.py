"""Build the ARM64 Debian application package on a Debian host."""
import json
import os
import shutil
import subprocess
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
PACKAGE = "rig-companion-zero"
VERSION = "0.7.0-1"


def build():
    root = SOURCE / "build" / PACKAGE
    if root.exists():
        if root.resolve().parent != (SOURCE / "build").resolve():
            raise RuntimeError("Invalid package build folder")
        shutil.rmtree(root)
    app = root / "usr/share/APPLaunch/lib" / PACKAGE
    app.mkdir(parents=True)
    shutil.copytree(SOURCE / "rigzero", app / "rigzero", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    subprocess.run(["g++", "-std=c++17", "-O1", "-shared", "-fPIC", "-o",
                    str(app / "rigzero" / "libpacket.so"), str(SOURCE / "native" / "packet_backend.cpp")], check=True)
    control = root / "DEBIAN"
    control.mkdir()
    (control / "control").write_text(f"""Package: {PACKAGE}
Version: {VERSION}
Architecture: arm64
Maintainer: TheTechNomad <https://www.TheTechNomad.com>
Section: hamradio
Priority: optional
Depends: python3, python3-pygame, python3-numpy, python3-dbus, python3-gi, python3-pil, bluez, fonts-dejavu-core, libstdc++6
Recommends: rig-companion-zero-radio-kenwood
Description: Rig Companion radio controls, APRS and D-STAR for CardputerZero
 Keyboard radio controls, native APRS receive, persistent messages,
 weather, packet tools, optional BLE/TCP KISS bridges and a D-STAR
 reflector terminal with QuadNet callsign routing. Radios come as separate
 radio packages (Kenwood, Benshi...), chosen on the splash screen. Beta.
""")
    launcher = """#!/bin/sh
export PYTHONPATH=/usr/share/APPLaunch/lib/rig-companion-zero
export PYGAME_HIDE_SUPPORT_PROMPT=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
exec /usr/bin/python3 -m rigzero "$@"
"""
    for relative in ("usr/bin/rig-companion-zero", "usr/share/APPLaunch/bin/rig-companion-zero"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(launcher)
        path.chmod(0o755)
    desktop = root / "usr/share/APPLaunch/applications/rig-companion-zero.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text("""[Desktop Entry]
Name=Rig Companion
Exec=bin/rig-companion-zero
Terminal=false
Icon=share/images/rig-companion-zero.png
Type=Application
Categories=HamRadio;
""")
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame
    pygame.font.init()
    icon = pygame.Surface((256, 256))
    icon.fill("#10191e")
    pygame.draw.rect(icon, "#202e35", (34, 55, 188, 148), border_radius=20)
    pygame.draw.rect(icon, "#65e2d2", (50, 72, 156, 55), border_radius=6)
    pygame.draw.lines(icon, "#10191e", False, [(60, 101), (90, 101), (101, 82), (114, 117), (128, 92), (139, 101), (196, 101)], 5)
    pygame.draw.circle(icon, "#65e2d2", (177, 164), 22, 5)
    pygame.draw.line(icon, "#65e2d2", (60, 50), (60, 22), 7)
    for x in (58, 82, 106):
        pygame.draw.line(icon, "#a8bfc3", (x, 157), (x, 177), 5)
    for relative in ("usr/share/APPLaunch/share/images/rig-companion-zero.png", "usr/share/icons/hicolor/256x256/apps/rig-companion-zero.png"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        pygame.image.save(icon, str(path))
    assets = SOURCE / "assets"
    assets.mkdir(exist_ok=True)
    pygame.image.save(icon, str(assets / "icon.png"))
    docs = root / "usr/share/doc" / PACKAGE
    docs.mkdir(parents=True)
    shutil.copy2(SOURCE / "README.md", docs / "README.md")
    shutil.copy2(SOURCE / "THIRD-PARTY.md", docs / "THIRD-PARTY.md")
    licence = SOURCE.parent / "LICENSE"
    if licence.exists():
        shutil.copy2(licence, docs / "copyright")
    shutil.copytree(SOURCE / "licenses", docs / "licenses")
    for notice in ("LICENSE-upstream.txt", "LICENSE-dart-http.txt"):
        shutil.copy2(SOURCE / "native" / notice, docs / "licenses" / ("packet-" + notice))
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.stat().st_mode & 0o111 == 0:
            path.chmod(0o644)
    output = SOURCE / "dist" / f"{PACKAGE}_{VERSION}_arm64.deb"
    output.parent.mkdir(exist_ok=True)
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(root), str(output)], check=True)
    print(output)
    for manifest in sorted((SOURCE / "radios").glob("*/radio.json")):
        build_radio(manifest.parent)


def build_radio(folder):
    """One radio package: /usr/share/APPLaunch/lib/rig-companion-zero/radios/<id>/."""
    info = json.loads((folder / "radio.json").read_text())
    name = f"{PACKAGE}-radio-{info['id']}"
    version = info["version"] + "-1"
    root = SOURCE / "build" / name
    if root.exists():
        shutil.rmtree(root)
    target = root / "usr/share/APPLaunch/lib" / PACKAGE / "radios" / info["id"]
    shutil.copytree(folder, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    models = ", ".join(radio["key"] for radio in info["radios"])
    (root / "DEBIAN").mkdir()
    (root / "DEBIAN" / "control").write_text(f"""Package: {name}
Version: {version}
Architecture: all
Maintainer: TheTechNomad <https://www.TheTechNomad.com>
Section: hamradio
Priority: optional
Depends: {PACKAGE} (>= 0.7.0)
Description: Rig Companion radio package: {info['name']}
 Adds {models} to Rig Companion. Pick it on the splash screen.
""")
    docs = root / "usr/share/doc" / name
    docs.mkdir(parents=True)
    licence = SOURCE.parent / "LICENSE"
    if licence.exists():
        shutil.copy2(licence, docs / "copyright")
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    output = SOURCE / "dist" / f"{name}_{version}_all.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(root), str(output)], check=True)
    print(output)


if __name__ == "__main__":
    build()

