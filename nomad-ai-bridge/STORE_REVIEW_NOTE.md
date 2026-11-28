## Nomad AI Bridge (NMAB) — notes for the reviewer

**What it is:** an MCP server that lets the owner's own AI app (Claude, ChatGPT, Codex, Claude Code and other MCP clients) use their CardputerZero remotely: run commands, edit files, take LCD screenshots, read device status. Developed by TheTechNomad (www.TheTechNomad.com, https://github.com/TeddyPete). Source: https://github.com/TeddyPete/NomadTools (GPL-3.0-only).

**Nothing runs as root**
- The app and its background server run as the normal user (`pi`): the server is a systemd **user** service, `nomad-ai-bridge.service`.
- The package installs **no** sudoers file.
- Admin (sudo) is optional and off by default. The owner sets it up once in Permissions > Admin (sudo) by typing the device password on the keyboard. The password goes to `sudo -S` once, is never saved, and is cleared from memory right away. The terminal equivalent is `sudo nomad-ai-bridge --enable-admin`.
- That setup adds one rule, checked with `visudo` before it is used. It allows only the app's own helper `/usr/lib/nomad-ai-bridge/rootrun`.
- Even with the rule in place, root is used only while the Admin switch is on. The title bar shows a red dot then.
- To remove it: Permissions > Remove Admin setup, or `sudo nomad-ai-bridge --disable-admin`. Purging the package also deletes the rule.

**Network / ngrok download**
- No ports are opened on the local network. The server listens on 127.0.0.1 only and reaches the internet through an outbound ngrok tunnel.
- The ngrok program is **not** bundled. During first setup, after the owner has made a free ngrok account, the app downloads the official ngrok binary from ngrok's own download server (bin.equinox.io) into `~/.local/lib/nomad-ai-bridge/`, as the pi user.
- Setup is 3 QR steps on the screen (see the 2nd screenshot):
  1. Scan to make the ngrok account.
  2. Scan to open the authtoken page.
  3. Scan a one-time page on the local network and paste the token from a phone.
- That local page runs only while step 3 is on screen. The device shows the token's last 4 characters, and the owner must press OK on the device to accept it.

**Who can connect**
- Claude and ChatGPT sign in with OAuth 2.1, and the owner approves each one with a **one-time code** shown on the device's screen.
- Codex, Claude Code and other MCP apps get a revocable **access key**. It is shown on the owner's computer only after the owner presses OK on the device, which displays that computer's address.
- **Connected apps** lists every app with when it was last used. The owner can disconnect one app, or disconnect everyone at once.
- **Remote access On/Off** on the home screen stops the server completely. Every tool call is logged on the device.

**Permissions declared:** network = true, background_service = true (the server can keep running after the app is closed, until it is switched off). Everything else is false.

**Demo video:** posted below. It shows the app launching, setup / connecting an AI app with a code, the AI running a status check, the Admin setup screen, and exiting with Esc.

Powered by ngrok. Not affiliated with ngrok, Anthropic or OpenAI.
