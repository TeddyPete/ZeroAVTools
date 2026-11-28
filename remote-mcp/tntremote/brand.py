"""Which app this build is. One source tree builds two separate apps that can be
installed side by side (scripts/build_deb.sh tnt|nomad writes this file):

  TNT MCP Link  (tnt-mcp-link)  Ted's own build; state folders keep the old
                                tnt-remote name; port 8765
  Nomad MCP     (nomad-mcp)     the public build: ngrok first, own folders,
                                own service, port 8775

Everything that must not clash between the two (folders, service, ports, root
helper) comes from here.
"""
APP_NAME = "TNT MCP Link"
CMD = "tnt-mcp-link"            # command / package name
STATE = "tnt-remote"            # folder name under ~/.config, ~/.local/state, ~/.local/lib, $XDG_RUNTIME_DIR
SERVICE = "tnt-remote.service"  # systemd user unit
PORT = 8765                     # server, 127.0.0.1 only
SETUP_PORT = 8766               # one-time ngrok token page on the LAN
ROOTRUN = "/usr/lib/tnt-remote/rootrun"
MIGRATE_CLOUDFLARE = True       # an existing Cloudflare token keeps the device on Cloudflare
FIRST_RUN_WIZARD = False
SETUP_PATH = "tntmcplink"      # the phone's token page: http://<ip>:<SETUP_PORT>/<SETUP_PATH>
OLD_STATE = ""                 # an earlier state folder name to move over on first start
CREDITS = ""                   # trademark / "not affiliated" line for Help and the welcome screen        # open the ngrok setup wizard when no tunnel is set up
DEVELOPER = ("Developed by TheTechNomad", "www.TheTechNomad.com", "https://github.com/TeddyPete")
TAGLINE = "Use the CardputerZero from your AI app, anywhere"
OWNER = "Ted's"                # whose device, in the AI's instructions
NET_TOOLS = "TNT AV Net Tools"  # the companion app that net_tool / av_device_info use
APPS_WORD = "TNT"               # "TNT apps" in help text
ADMIN_SETUP = False              # True: package ships no sudo rule; the owner adds it once (adminsetup.py)
