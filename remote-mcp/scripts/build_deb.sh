#!/usr/bin/env bash
# Builds the APPLaunch .deb: build/tnt-mcp-link_<ver>_all.deb
# (TNT MCP Link was called TNT Remote; the package replaces tnt-remote. Internal
# names that hold state keep "tnt-remote": the systemd user unit, the sudoers
# drop-in, the root helper and ~/.config|.local/state/tnt-remote.)
# Needs: dpkg-deb, python3. Run from anywhere (use an LF checkout).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
PKG=tnt-mcp-link
OLD=tnt-remote
VER=$(python3 -c "import json;print(json.load(open('app-builder.json'))['version'])")
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
chmod 755 "$ROOT"
A="$ROOT/usr/share/APPLaunch"
install -d "$ROOT/DEBIAN" "$A/applications" "$A/bin" "$A/share/images" "$A/lib/$PKG/tntremote" "$ROOT/usr/bin" \
  "$ROOT/usr/lib/$OLD" "$ROOT/usr/lib/systemd/user" "$ROOT/etc/sudoers.d"
install -m 644 packaging/$PKG.desktop "$A/applications/$PKG.desktop"
install -m 755 packaging/$PKG "$A/bin/$PKG"
ln -s /usr/share/APPLaunch/bin/$PKG "$ROOT/usr/bin/$PKG"
ln -s /usr/share/APPLaunch/bin/$PKG "$ROOT/usr/bin/$OLD"      # old command name keeps working
install -m 644 packaging/icon_100.png "$A/share/images/$PKG.png"
install -m 644 tntremote/*.py "$A/lib/$PKG/tntremote/"
sed -i "s/^__version__ = .*/__version__ = \"$VER\"/" "$A/lib/$PKG/tntremote/__init__.py"
install -m 755 packaging/rootrun "$ROOT/usr/lib/$OLD/rootrun"
install -m 644 packaging/$OLD.service "$ROOT/usr/lib/systemd/user/$OLD.service"
install -m 440 packaging/sudoers "$ROOT/etc/sudoers.d/$OLD"
for s in preinst postinst prerm; do install -m 755 packaging/$s "$ROOT/DEBIAN/$s"; done
echo "/etc/sudoers.d/$OLD" > "$ROOT/DEBIAN/conffiles"
SIZE=$(du -sk "$ROOT/usr" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<CTL
Package: $PKG
Version: $VER
Architecture: all
Maintainer: TeddyPete <299527578+TeddyPete@users.noreply.github.com>
Depends: python3, python3-pygame (>= 2.1), python3-numpy, sudo, systemd
Recommends: tnt-av-net-tools, fonts-dejavu-core
Replaces: tnt-remote
Conflicts: tnt-remote
Provides: tnt-remote
Installed-Size: $SIZE
Section: net
Priority: optional
Homepage: https://TheTechNomad.com
Description: TNT MCP Link: use the CardputerZero from your AI app, anywhere
 Part of the TNT (Tech Nomad Ted) tools, TheTechNomad.com.
 An MCP server that lets AI apps (Claude, ChatGPT developer mode, OpenAI
 Codex and other MCP clients) run commands, read and change files, install
 and run programs and use TNT AV Net Tools on this CardputerZero, wherever
 it is plugged in. It connects out through an ngrok or Cloudflare tunnel
 (no open ports) and signs each app in with OAuth plus a one-time pairing
 code shown on the device. QR codes on the screen walk through setup.
 .
 On/off switch, live activity and permission switches on the built-in
 screen; other TNT apps show a dot in their title bar while it is on.
 Admin (sudo) mode uses a root helper allowed by /etc/sudoers.d/tnt-remote
 and is only used while switched on at the device.
 Formerly TNT Remote (package tnt-remote); upgrading keeps all settings.
CTL
mkdir -p build
OUT="build/${PKG}_${VER}_all.deb"
dpkg-deb --root-owner-group --build "$ROOT" "$OUT" >/dev/null
echo "$OUT"
