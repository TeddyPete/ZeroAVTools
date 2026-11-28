# Third-party components

Retrieved November 27, 2026. External source files are reference material and were inspected before use.

## APRS packet parser

- Author: Tim Cocks for Adafruit Industries, copyright 2026.
- Source: https://github.com/adafruit/Adafruit_Learning_System_Guides/blob/main/Fruit_Jam/Fruit_Jam_SDR_Mapping/aprs_receiver/aprs_parse.py
- Git blob SHA: `1338ef829749e8bc85337f2e81eab2634735e511` (file identity, not a repository commit).
- License: MIT; full text in `licenses/MIT.txt`. File copyright and SPDX header are preserved.
- Copy: `rigzero/vendor/aprs_parse.py`, unchanged.
- Used for standard/compressed positions, Mic-E, objects/items, weather, station symbols and report classification. Rig Companion adds readable telemetry fields, persistent station definitions, validation, summaries and PHG interpretation.

## Map projection helpers

- Author: komoot GmbH, copyright 2015.
- Source: https://github.com/komoot/staticmap/blob/master/staticmap/staticmap.py
- Git blob SHA: `7378ec2ad67d203457530cd065388480eeba2547`.
- License: Apache-2.0; full text in `licenses/Apache-2.0.txt`.
- Copy: four coordinate functions extracted unchanged into `rigzero/vendor/projection.py`; unused rendering/network dependencies were omitted. The tile cache and small-screen interface are Rig Companion code.

## Basemap tiles

- © OpenStreetMap contributors, https://www.openstreetmap.org/copyright .
- Policy checked November 27, 2026: https://operations.osmfoundation.org/policies/tiles/ .
- The map requests only currently visible tiles over HTTPS, identifies the app, honors caching headers and displays attribution. It does not prefetch regions. Previously viewed cached areas work offline; an uncached offline view shows station markers without geographic map detail.

No aprs.fi code, branding, API or historical database is used. Full Linux APRS clients reviewed: https://github.com/Xastir/Xastir and https://www.ka2ddo.org/ka2ddo/YAAC.html . They remain separate apps, not bundled components.
## Connected packet tools

The connected AX.25, Winlink B2F, and YAPP cores under `native/` are reused from
the Tab5 Rig Companion / HTCommander handoffs. They derive from HTCommander by
Ylian Saint-Hilaire and contributors, pinned revision
`50434c0369b404cca1fc72d8a4c1a58aa75e9f9c`. The original Apache 2.0 and Dart
notices are included. `native/packet_backend.cpp` adapts those engines to
the Cardputer Zero's Linux filesystem and shared radio worker.


## D-STAR protocols and reflector lists

- `rigzero/dstar.py` is Rig Companion code written from the public protocol
  behaviour of MMDVMHost and ircDDBGateway (Jonathan Naylor G4KLX, GPL-2.0-or-later)
  and DroidStar (Doug McLain AD8DP, GPL-3.0): MMDVM serial framing, D-STAR header
  CRC, slow data, DExtra, DCS and DPlus packets, and the D-Plus registration ping.
  No source files are copied.
- `rigzero/dstar_route.py` (callsign routing) follows the ircDDB client and G2
  behaviour of ircDDBGateway (ircDDB library by Michael Dirska DL1BFF and
  Jonathan Naylor G4KLX, GPL-2.0-or-later). No source files are copied. It
  connects to the QuadNet ircDDB server (ircv4.openquad.net:9007) at run time.
- Reflector addresses are downloaded at run time from the Pi-Star host files
  (https://www.pistar.uk/downloads/) and cached under `~/.cache/rig-companion-zero/dstar-hosts`.
- Optional name / city lookups for US callsigns use https://callook.info (public FCC data).
