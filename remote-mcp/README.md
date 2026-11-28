# TNT MCP Link

Part of the TNT (Tech Nomad Ted) tools, [TheTechNomad.com](https://TheTechNomad.com). Formerly *TNT Remote*.

TNT MCP Link turns the M5Stack CardputerZero into a **remote MCP server**. AI apps that speak MCP can then use the device from anywhere it has internet:
* Claude (the phone app, claude.ai, Claude Code)
* ChatGPT (developer mode apps/connectors)
* OpenAI Codex
* other MCP clients

The connected app can:

* run commands and long background jobs (`run_command`, `start_background`, ...), and run them as root with `sudo: true` while **Admin (sudo)** is on
* read, write, edit, move and delete files, and list folders
* take a screenshot of the built-in 320x170 screen
* run TNT AV Net Tools checks (`net_tool`) and AV device info pulls (`av_device_info`) on the network the device is plugged into

## How it connects

The server listens only on `127.0.0.1:8765`. A tunnel program dials **out** from the device, so no ports are opened on the network it is plugged into. You pick the tunnel in **Setup > Tunnel**:

| Tunnel | Address | Setup |
|---|---|---|
| **ngrok** (default for new devices) | `https://<name>.ngrok-free.dev/mcp`, fixed for good | A free ngrok account. The device screen shows QR codes for the signup page and the authtoken page, and a third QR code opens a page on the device where your phone pastes the token. |
| **Cloudflare** (advanced) | your own: `https://<worker>.<you>.workers.dev/mcp` or a domain | A Cloudflare tunnel token plus a Worker / Workers VPC (see below) or a public hostname |
| **Quick test** | `https://<random>.trycloudflare.com/mcp` | No account. The address **changes on every restart**, so it is only for trying it out. |

**Setup > Connector address** shows the final address as text and as a QR code.

A device upgraded from TNT Remote keeps its Cloudflare tunnel and address. Only a device that has never been set up starts on ngrok.

```
AI app ──https──> ngrok edge ────────────────────────────────> ngrok agent on the Pi ──> 127.0.0.1:8765
AI app ──https──> Worker (workers.dev) ──Workers VPC──> Cloudflare Tunnel ──> cloudflared on the Pi ──> 127.0.0.1:8765
```

**Sign-in.** OAuth 2.1 runs on the device:
* Clients register themselves with dynamic client registration and use PKCE S256.
* The authorization response includes `iss` (RFC 9207), which ChatGPT and Codex check.
* Redirects are allowed only to:
  * Claude: `claude.ai` / `claude.com /api/mcp/auth_callback`
  * ChatGPT: `chatgpt.com/connector_platform_oauth_redirect` or `chatgpt.com/connector/oauth/<id>`
  * localhost (Claude Code, Codex)
* To approve a connection, you type a **one-time pairing code** that the device shows on its own screen. A code is valid for 10 minutes and one use, and 5 wrong tries cancel it.
* Tokens are stored only as SHA-256 hashes. Access tokens last 1 hour. Refresh tokens last 60 days and rotate on each use, so an app that is used stays signed in.

On the free ngrok plan, a web browser may first see an ngrok "You are about to visit" page on the Allow step. Tap **Visit Site**. The AI apps' own requests don't get that page.

* Every tool call is logged to `~/.local/state/tnt-remote/audit.log`. The device's Status screen shows live activity.
* Turning **Remote access** off stops the server and the tunnel at once. **Disconnect everyone** forgets all sign-ins.
* While it is on, TNT apps that use the shared `tntui.py` show a dot at the right end of the title bar: orange when it is on, red when Admin (sudo) is also on.

**Security note:** the permission switches stop accidents and misuse by a client, but they are not a hard boundary. Anything the AI runs as `pi` could change them. Treat *Remote access On* as handing the connected AI apps full control of the device.

## Adding it to your AI app

First get the address from **Setup > Connector address**. It ends in `/mcp`.

* **Claude** (claude.ai or the app): go to Settings > Connectors > Add custom connector. Paste the address and click Connect. On the Allow page that opens, type the code from **Pair new connection** on the device.
* **ChatGPT** (web, on a plan with developer mode):
  1. Go to Settings > Apps & Connectors > Advanced and turn on **Developer mode**.
  2. Back in Apps & Connectors, choose **Create**. Name it *TNT MCP Link*, paste the address as the MCP server URL, and set Authentication to **OAuth**.
  3. Tick that you trust the app, then click Create.
  4. When the device's Allow page opens, type the pairing code.
  5. In a chat, turn the app on from the + / Developer mode menu.
* **Codex CLI**: run `codex mcp add tnt --url https://<address>/mcp`, then `codex mcp login tnt`. Your browser opens the Allow page; type the pairing code there.

## On the device

Open `TNT MCP Link` in the launcher:

| Item | What it does |
|---|---|
| Remote access | On/Off. On also means it starts at boot (systemd user service `tnt-remote`, `pi` lingers). |
| Pair new connection | Shows the one-time code for the Allow page |
| Status & activity | Tunnel state, connector address, what the AI did recently |
| Connected | Signed-in clients, plus *Disconnect everyone* |
| Permissions | Run commands, **Admin (sudo)**, Change files, Net Tools |
| Setup | Tunnel type, Connector address (QR), ngrok setup, Cloudflare address/token/cloudflared, recent tool calls |

The keys follow the TNT standard:
* F/X (or the Fn arrows) move.
* OK or → selects.
* ← or Esc goes back.
* Esc quits on the main page.

Command line, over SSH:

```
tnt-mcp-link --status | --url | --on | --off | --pair | --tunnel ngrok|cloudflare|quick
             --set-ngrok-token (stdin) | --install-ngrok
             --set-url URL | --set-token (stdin) | --install-cloudflared
             --revoke-all | --log
```

The old `tnt-remote` command still works.

## Cloudflare setup (advanced, done once)

1. Cloudflare, from a PC where wrangler is logged in:
   ```
   npx wrangler tunnel create tnt-cp0
   npx wrangler vpc service create tnt-cp0 --type http --tunnel-id <TUNNEL_ID> --ipv4 127.0.0.1 --http-port 8765
   # put the service id in worker/wrangler.jsonc, then
   cd worker && npx wrangler deploy        # -> https://cp0.<you>.workers.dev
   ```
2. On the Pi:
   * Run `tnt-mcp-link --tunnel cloudflare`.
   * Run `tnt-mcp-link --install-cloudflared`.
   * Run `tnt-mcp-link --set-url https://cp0.<you>.workers.dev`.
   * Load the tunnel token with `tnt-mcp-link --set-token` on stdin. The API endpoint for the token is `GET /accounts/<acct>/cfd_tunnel/<id>/token`.

The tunnel works over QUIC (UDP 7844) or HTTP/2 (TCP 7844). The VPC Service points at the IP `127.0.0.1`, not a name, so HTTP/2-only networks work too.

## Packaging and upgrade

`scripts/build_deb.sh` writes `build/tnt-mcp-link_<ver>_all.deb`. The package `Replaces`/`Conflicts`/`Provides` the old `tnt-remote` package.

The internal names that hold state keep `tnt-remote`, so an upgrade keeps all settings, sign-ins and the tunnel:
* the user unit `tnt-remote.service`
* `/etc/sudoers.d/tnt-remote`
* `/usr/lib/tnt-remote/rootrun`
* `~/.config/tnt-remote`
* `~/.local/state/tnt-remote`

Removing the old package switches its service off. The new `preinst`/`postinst` notice that and switch it back on.

Memory use: about 24 MB for the stdlib server, about 38 MB for cloudflared, and about 30 MB for ngrok.

## Nomad AI Bridge

`../nomad-ai-bridge/` is the public build of the same code: **Nomad AI Bridge** (package `nomad-ai-bridge`, formerly Nomad MCP). It sets up ngrok first. Only `tntremote/brand.py` differs. It defines the app name, the folders, the systemd unit, the ports and the root helper, so the two apps can be installed side by side.

## Files

* `tntremote/server.py`: HTTP server (OAuth + MCP endpoints) and the tunnel supervisor (ngrok, cloudflared, quick tunnel)
* `tntremote/oauth.py`: client registration, pairing codes, tokens
* `tntremote/mcp.py`: MCP JSON-RPC and the tool list
* `tntremote/tools.py`, `nettools.py`, `netrun.py`, `fbshot.py`: the tools
* `tntremote/app.py`: the LCD app, including the tunnel choice and the QR setup screens. `qr.py` draws the QR codes, and `setupweb.py` is the one-time page where the phone pastes the ngrok token.
* `tntremote/tntui.py`: the shared TNT UI, with the remote dot
* `packaging/`: the APPLaunch wrapper, the user service, the root helper `rootrun`, the sudoers drop-in and the maintainer scripts
* `worker/`: the Cloudflare Worker relay
