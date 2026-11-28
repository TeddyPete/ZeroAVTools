"""Nomad AI Bridge build of the shared server/app code in ../remote-mcp/tntremote.
scripts/build_deb.sh copies this over tntremote/brand.py in the package, so
Nomad AI Bridge has its own folders, service, ports and root helper and can be
installed next to TNT MCP Link. It was called Nomad MCP until 0.2.0."""
APP_NAME = "Nomad AI Bridge"
CMD = "nomad-ai-bridge"
STATE = "nomad-ai-bridge"
OLD_STATE = "nomad-mcp"         # folders of the Nomad MCP name, moved over on first start
SERVICE = "nomad-ai-bridge.service"
PORT = 8775
SETUP_PORT = 8776
SETUP_PATH = "aibridge"         # phone token page: http://<ip>:8776/aibridge
ROOTRUN = "/usr/lib/nomad-ai-bridge/rootrun"
MIGRATE_CLOUDFLARE = False      # new app: always starts on ngrok
FIRST_RUN_WIZARD = True         # no tunnel yet: open straight into the ngrok setup wizard
CREDITS = ("Powered by ngrok. Not affiliated with ngrok, Anthropic (Claude) or OpenAI "
           "(ChatGPT, Codex); those names are their owners' trademarks.")
DEVELOPER = ("Developed by TheTechNomad", "www.TheTechNomad.com", "https://github.com/TeddyPete")
TAGLINE = "Let your AI connect to and control your Cardputer Zero"
OWNER = "the owner's"
NET_TOOLS = "Nomad Net Tools"
APPS_WORD = "Nomad"
ADMIN_SETUP = True               # store rule: no root by default; owner grants Admin once with the pi password
THEME = "cyber"                  # LCD look: near-black, neon cyan + magenta, monospace (0.2.11)
