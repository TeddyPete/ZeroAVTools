# Nomad AI Bridge

**Let your AI connect to and control your Cardputer Zero.** An MCP server for Claude, ChatGPT & Codex, by Tech Nomad Ted, [TheTechNomad.com](https://TheTechNomad.com). Formerly *Nomad MCP*.

*Powered by ngrok. Not affiliated with ngrok, Anthropic or OpenAI. Claude, ChatGPT and Codex are trademarks of their owners.*

Nomad AI Bridge turns an M5Stack CardputerZero into a remote **MCP server**. You plug it in anywhere with internet, and these apps can use it:
* Claude (phone app, claude.ai, Claude Code)
* ChatGPT (developer mode)
* OpenAI Codex
* any other MCP client

The connected app can:

* run commands and long background jobs, and run them as root while **Admin (sudo)** is switched on at the device
* read, write, edit, move and delete files
* take a screenshot of the built-in screen
* run TNT AV Net Tools checks when that app is installed

## Setup on the device (about 3 minutes)

1. Open **Nomad AI Bridge**. The first time, it shows what the app does and how, with **Start setup** already selected. Press OK. Later you can rerun it from **Setup > ngrok setup**. Three QR codes follow:
   1. Scan it and make a free ngrok account, or sign in. After sign-up, choose **Share localhost**. Then press OK.
   2. Scan it to open your ngrok authtoken page. ngrok lists 4 steps; only step 2, **Add your authtoken**, matters. Copy the token (the part after `ngrok config add-authtoken`). Then press OK.
   3. Scan it with your phone, on the same Wi-Fi/LAN as the device, or type the address shown beside it, like `192.168.1.10:8776/aibridge` (or `cp0.local:8776/aibridge`). Paste the token and tap Save. The device then shows the token's last 4 characters, and the phone shows the same 4. If they match, press OK on the device to use it. Pasting the whole `ngrok config add-authtoken ...` line also works. If your phone can't reach the device, press OK and type the token on the keyboard instead.
3. It switches Remote access on and shows your **connector address**, `https://<name>.ngrok-free.dev/mcp`, as text and as a QR code. This address is fixed and stays the same after restarts. Press OK there for the last step, **pair your app**: add the address in your AI app (see below), click Authenticate or Connect, and type the code the device shows on the Allow page. When the app signs in, the device shows **Connected!**

No account? Pick **Quick test** instead. It works right away, but the address changes every time the device restarts. **Cloudflare** is there for people who already run Cloudflare tunnels. Its command-line steps are in [../remote-mcp/README.md](../remote-mcp/README.md).

## Add it to your AI app

On the device choose **Connect an AI app** (the setup wizard goes there by itself), then pick the app. There are two ways in. The device picks the right one for each app:

* **Claude and ChatGPT: sign in with a pairing code.** They sign in with OAuth, so Claude's and ChatGPT's cloud can use it too.
  * **Claude** (phone, desktop, claude.ai): Settings > Connectors > Add custom connector. Paste the connector address and click Connect.
  * **ChatGPT** (web): click **Plugins** in the left sidebar ([chatgpt.com/plugins](https://chatgpt.com/plugins)), then **Add** (top right) > **Add custom MCP server**. Name it *Nomad AI Bridge* (the description is optional), set Connection to **Server URL** (not Tunnel) and paste the address, set Authentication to **OAuth**, tick **I understand and want to continue**, and click **Create as a plugin**. On the Allow page, type the code the device shows. In a new chat, type **@** and pick the plugin. Custom MCP servers need a ChatGPT Business, Enterprise or Edu plan (an admin may have to allow them); Pro gets read/fetch only.
  * When the **Allow** page opens, type the code the device shows. With the free ngrok plan your browser may first show an ngrok "You are about to visit" page; click **Visit Site**.
* **Codex, Claude Code and other MCP apps: an access key.** No sign-in, so it works the first time in every chat, in the CLI and in cloud agents. The device makes a key for that app and shows a short address such as `192.168.1.10:8776/aibridge`. Open it on your computer (same Wi-Fi). Press **OK** on the device to let that computer see the key. The page then shows ready-to-copy setup:
  * **Your private address** `https://<name>.ngrok-free.dev/k/<key>/mcp`. Paste it as the server URL in any app. For the **Codex app**: Settings > MCP servers > Add server, type Streamable HTTP, URL = the private address.
  * **Codex CLI/IDE**: `codex mcp add nomad --url "<private address>"`.
  * **Claude Code**: `claude mcp add --transport http --scope user nomad "<private address>"`.
  * Apps that set headers can use the normal address with `Authorization: Bearer <key>` (or `X-Nomad-Key: <key>`).
  * No computer on the same network? Press OK on the access key screen for a QR code of the private address.

Every app, paired or keyed, is listed under **Connected apps** with when it was last used, and can be removed there. Removing a key stops it at once.

## Safety

* Nomad AI Bridge only dials **out**, so it opens no ports on the network.
* The token page only runs while setup step 3 is on the screen, and a pasted token is used only after you press OK on the device.
* Every app has to be approved at the device: with a one-time code shown on its screen (valid 10 minutes, one use), or with an access key that is only shown to a computer you OK on the device.
* Treat an access key or private address like a password. Remove it under Connected apps if it leaks.
* Tokens are stored only as hashes. Refresh tokens last 60 days and renew while the app is in use.
* **Connected apps** lists which AI apps are signed in (Claude, ChatGPT, Codex...), when each was paired and last used. Pick one to disconnect just that app, or choose **Disconnect everyone**.
* **Remote access Off** cuts everyone off at once.
* Admin (sudo) stays off until you switch it on at the device. The title bar dot turns red while it is on.
* Every tool call is logged to `~/.local/state/nomad-ai-bridge/audit.log`.
* The switches are guardrails, not a hard boundary. Treat Remote access On as giving the connected app full control of the device.

## Command line (over SSH)

```
nomad-ai-bridge --status | --url | --on | --off | --pair | --tunnel ngrok|quick|cloudflare
          --set-ngrok-token (stdin) | --install-ngrok | --revoke-all | --log
```

## Renamed from Nomad MCP

Installing `nomad-ai-bridge` over `nomad-mcp` replaces it and moves its folders (`~/.config`, `~/.local/state`, `~/.local/lib` `/nomad-mcp`) to `nomad-ai-bridge`, so the ngrok token, the address and every paired app keep working. The old `nomad-mcp` command still works.

## How it is built

Nomad AI Bridge uses the same code as TNT MCP Link (`../remote-mcp/tntremote`). Only `brand.py` differs. That file gives Nomad AI Bridge its own:
* config folders (`~/.config/nomad-ai-bridge`, `~/.local/state/nomad-ai-bridge`)
* user service (`nomad-ai-bridge.service`)
* ports (server `127.0.0.1:8775`, token page `:8776`)
* root helper (`/usr/lib/nomad-ai-bridge/rootrun`)

That is why both apps can be installed side by side.

`scripts/build_deb.sh` writes `build/nomad-ai-bridge_<ver>_all.deb`.
