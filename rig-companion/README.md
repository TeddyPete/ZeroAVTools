# Rig Companion for CardputerZero

## 0.7.1 Nearby reflectors

D-STAR > 5 Reflectors > 5 Type now starts with **Nearby**: reflectors sorted by
distance from you, with miles and the server's city. Your position comes from
the TH-D75 GPS, else **My grid** (D-STAR Setup), else the saved beacon
position, else your callsign's grid (US calls, callook.info). Reflector lists
only hold server addresses, so the app looks each address up once with the free
ip-api.com batch service (about 2 minutes the first time, then cached 30 days).
That is where the server is, which is usually but not always near its owner.

## 0.7.0 Radio packages, splash screen and USB CAT

Radios are now separate packages. At launch a splash screen lists the radio
packages that are installed, and Rig Companion loads only the one you pick.
Change it later under **Link > Radio package**.

| Package | Debian package | Radios | Links |
|---|---|---|---|
| Kenwood | `rig-companion-zero-radio-kenwood` | TH-D75, TH-D74, TM-D750 | Bluetooth or USB cable |
| Benshi | `rig-companion-zero-radio-benshi` | UV-Pro, GA-5WB, VR-N76/N75/N7500/N7600, RT-660 | Bluetooth |

A radio package is a folder under
`/usr/share/APPLaunch/lib/rig-companion-zero/radios/<id>/` with a `radio.json`
(the radios, their features, Bluetooth service, USB baud rate) and a
`driver.py` that defines `Driver`, a subclass of `rigzero.radio.Radio`. New
brands (Yaesu, Icom...) are added the same way. `python3 package.py` builds the
app and every package under `radios/`.

**USB CAT.** Packages that list `usb` show plugged-in USB serial ports
(`/dev/serial/by-id`, `ttyACM`, `ttyUSB`) under **Link > Find ... / USB** next to
Bluetooth radios. No root is needed; the pi user is in the dialout group.

The About screen now reads "Developer: Ted Peterson" and fits the screen.

## 0.6.0 Choose your radio

Rig Companion now drives more than one radio. (Replaced by radio packages in 0.7.0.) Each radio loads its own controls, but the screens,
layout and keys stay the same.

| Radio | Driver | What works |
|---|---|---|
| Kenwood TH-D75 | Kenwood CAT + KISS over Bluetooth SPP | Everything, including D-STAR and GPS time |
| Kenwood TH-D74, TM-D750 | Kenwood | APRS, KISS, packet tools |
| BTECH UV-Pro / UV-50Pro, Radioddity GA-5WB / DB50-B, Vero VR-N76 / N75 / N7500 / N7600, Radtel RT-660 | Benshi over Bluetooth (GAIA service) | Channel up/down with names, volume, squelch, dual watch, APRS receive and send, packet tools through the radio's built-in TNC |

Benshi radios work from memory channels, so Up/Down steps through the
programmed channels instead of typing a frequency. D-STAR and GPS time only
show for the TH-D75. Close the HT phone app before pairing a Benshi radio, since
the radio takes one control connection at a time.

The Benshi protocol notes come from benlink and HTCommander (both Apache-2.0).
No source is copied. Hardware testing with a real Benshi radio is still to do.

## 0.5.1 D-STAR callsign routing

Direct calls to one callsign, the way a Pi-Star hotspot does it. When the
D-STAR gateway starts, Rig Companion logs in to **QuadNet** (the open ircDDB
network, ircv4.openquad.net, no registration) as your callsign and tells it
where you are, so other hotspots and repeaters can call you.

- **From the radio**: put a callsign in UR (e.g. `W1ABC`) and key up. The
  gateway looks up where that station was last heard and sends your voice
  straight to their gateway (G2, UDP port 40000).
- **From the keyboard**: **D** on the D-STAR screen opens Direct call. Type a
  callsign, **Enter** looks it up (last repeater, gateway, online or not),
  **8 Talk** makes your normal CQCQCQ transmissions go to that station, **7
  Clear** stops. Linking a reflector clears it too.
- **Calls to you** play on the radio. They need UDP port **40000** forwarded on
  your router to the Cardputer; calls you make work without that.
- If a station isn't found, the radio shows "W1ABC not on QuadNet" after you
  unkey. Setup has **Callsign routing (QuadNet) On/Off** and **My module**
  (B by default, the letter others see after your call).

## 0.5.0 D-STAR Reflector Terminal

**R** (or **8 Link → D-STAR reflectors**) opens D-STAR. The TH-D75 becomes a
D-STAR terminal: its own AMBE vocoder handles audio, and Rig Companion links
it to REF, XRF, XLX and DCS reflectors over the Cardputer's network.

On the radio: band A in DV mode, **Menu 650** DV Gateway Mode = Reflector
TERM Mode, **Menu 651** = your callsign, **Menu 985** DV Gateway = Bluetooth
(or USB). Then on the Cardputer: **8 Start**, **5 Link**, pick a reflector and
module, and talk with UR = CQCQCQ. The radio transmits nothing on RF in this
mode, and the TH-D75 has no Access Point mode.

- **Dashboard**: link state, who is talking now (callsign, name and city for
  US calls from callook.info, the 20-character D-STAR message, RX/TX timer),
  and the last stations heard.
- **Reflectors**: favorites and recent links, then the full REF / XRF / XLX /
  DCS lists from the Pi-Star host files (cached; **8** updates). **6** finds,
  **7** adds a favorite. The last reflector relinks on start (Setup).
- **Last heard**: up to 60 transmissions with message, duration, route and
  D-PRS position. **5** shows a station on the map.
- **D-PRS**: positions in slow data (`$$CRC` APRS lines or NMEA) become APRS
  stations, so they appear on the existing map and APRS list.
- **From the radio**: UR `REF001CL` links REF001 C, seven spaces + `U`
  unlinks, + `E` records and plays back an echo test, + `I` shows the link.
- REF reflectors (DPlus) need your callsign registered at
  regist.dstargateway.org to transmit; otherwise they link listen-only. XRF,
  XLX and DCS need no registration. Callsign routing: see 0.5.1.
- While D-STAR runs it owns the radio link; CAT controls and KISS resume when
  you stop it (**8**).

The radio link is the TH-D75's MMDVM-style serial gateway on Bluetooth
RFCOMM channel 2 (channel is selectable in Setup) or USB `/dev/ttyACM*`.

## 0.4.0 Packet Tools

**U** opens Packet Tools. Use F/X or the arrows to move and Enter to open.
Monitor filters by callsign, type and RX/TX. It can observe other packet
sessions and export the current filter to CSV/PCAP. **Q** on Radio opens
volume, squelch, power, band, mode, channel mode and dual receive controls.
The screen shows values read from the radio.

- **AX.25 Terminal** uses the radio's current packet channel. Set the peer,
  review the RF confirmation, then call. Use Write for lines, Stop to
  disconnect, S to save the transcript, Y to send a YAPP file, and L to
  allow incoming files. Terminal logs and YAPP files use
  `~/Documents/Rig-Companion-Transfers`. Files are limited to 2 MiB.
- **Winlink Mail** has Inbox, Drafts, Outbox, and Sent. Gate lets you save a
  gateway callsign and optional MHz frequency. Pass accepts the Winlink
  password for the current app run. Use the radio's tuned channel for models
  without verified remote tuning. The Winlink engine reads and commits its
  mail independently of the display. Attachments are retained in incoming
  stored messages. The current keyboard editor handles text messages.
- **APRS Tools** groups station conversations, manually saved contacts,
  WXBOT requests, the acknowledgment outbox, and beacon setup. A recent
  TH-D75 position fix can be sent once as a position report. Get the fix
  before entering KISS, then use the GPS beacon action within 30 seconds.
- **Phone / PC Bridge** retains the RadioMail BLE and TCP KISS options.
  The radio permits one KISS session owner. At the end, wait for the
  restoration result on the Radio page.

The native engine is built by `package.py` with Debian's C++ compiler.
For source-tree testing, build `native/packet_backend.cpp` as
`rigzero/libpacket.so` with `g++ -std=c++17 -shared -fPIC`.

## 0.3.0 Kenwood companion

This build keeps the existing package ID and station database. **G** opens
Field Manual and **U** opens APRS / Packet setup. Both are also under **8 Link**.

**8 Link → GPS time from TH-D75** reads UTC from the radio. Enable built-in
GPS (400), PC Output (405), GPRMC (406), and Bluetooth GPS output (981) on
the handset. After two advancing fixes pass validation, packet timestamps
use GPS UTC, including during KISS holdover. Enter on the GPS screen requests
a one-time Cardputer system clock update when a fresh fix is available and
the OS permits it. The app leaves NTP configured as found.

- Field Manual has a searchable 216-item TH-D75 menu index, key combinations,
  PF key notes and operating tips. **8 Model** switches to starter guides
  for the TH-D74 and TM-D750.
- Link discovers known models and other devices advertising Bluetooth Serial
  Port Profile. An exact CAT ID sets the model. If the ID is unrecognized or
  missing, pairing can still be used with Handset KISS monitor.
- Automatic KISS requires a recognized CAT identity and validated radio
  readbacks. Direct CAT writes and automatic gateway retuning use the TH-D75
  profile. With Handset KISS monitor, start and stop KISS on the handset.
  The app receives frames and sends no TNC commands in this mode.
- Packet setup selects band and rate for the next local KISS session. It
  displays the confirmed frequency before send. Export CSV and PCAP under
  `~/Documents/Rig-Companion-Captures`. PCAP holds exact bytes newly captured
  through KISS. Older and native APRS text records are included in CSV.
- Beacon Setup sends a fixed position once or repeats at 5/10/20 minutes
  with explicit confirmation and local 1200 bps KISS. Ending KISS, closing
  the app or losing the link clears the schedule. The handheld's GPS and
  SmartBeaconing setup is documented in the Field Manual.

Kenwood sources: [TH-D75](https://manual.kenwood.com/en_contents/attachDownload/109792/),
[TH-D74](https://manual.kenwood.com/en_contents/attachDownload/98115/),
[TM-D750](https://manuals.jvckenwood.com/download/files/B5A-4729-00_01.pdf).

## Existing radio and packet functions

Version 0.2.1 adds arrow-key navigation in the APRS lists and packet detail (0.2.0 added readable APRS reports and a station map). It uses the CYD project's
verified CAT conventions and KISS framing with Linux BlueZ, a keyboard UI and
SQLite storage. Pairing, both VFO readbacks and live KISS reception have been
checked on the user's TH-D75. RadioMail, APRS transmit and native APRS receive
still need qualification on this port.

## Connections

Open Link with **8**, choose **Find TH-D75 / pair**, then open Menu **934** on
the handheld. Select the discovered radio and compare the pairing numbers.
Confirm on both devices. The app remembers the chosen radio and reconnects.
Frequency and controls become available after an exact `ID TH-D75` response.

For native receive, enable APRS on the handheld, set Menu **590** to **Raw
Packets**, and Menu **982** to **Bluetooth**. For KISS set Menu **983** to
**Bluetooth**. The radio's existing USB/Bluetooth menu destinations are set
on the handheld. The app does not claim a CAT command for those menu entries.

## Keys

The keys below the screen switch pages, **4 Radio**, **5 APRS**, **6 Inbox**,
**7 Weather**, **8 Link**. Arrow keys and F/Z/X/C navigate outside editors.
Inside an editor all printable keys enter text. Esc backs out. Esc from Radio
opens exit confirmation, and the app restores an active KISS session on exit.

On Radio, up/down tunes or steps populated memory channels. Enter opens MHz
entry, Tab selects A/B, M cycles mode, P cycles power, V toggles VFO/memory,
D toggles dual receive, S cycles squelch, and J/K changes volume. **?** shows
the control guide. Readback is required after changes.

On APRS/Inbox/Weather, Up/Down move, Right or Enter opens the selected packet,
Left backs out, N opens compose, K starts or ends KISS, and S switches
live/saved packets on the APRS page. In packet detail Up/Down scroll and Left
backs out; the bottom keys are **4 Back**, **5 Map**, **6 Save**, **7 Reply**,
**8 Raw** (switches between readable and raw content). Every received
sender can be used as an APRS recipient. Messages and packets carry the Zero's
UTC receive timestamp. Clock accuracy follows the device's system clock.

Set your licensed callsign and SSID under **Link > Callsign**. Compose pre-fills
From, and Reply pre-fills To. Tab switches between recipient and body. Enter
starts KISS if needed, then opens a send confirmation. Enter in that confirmation
sends one packet. Local messages request an APRS acknowledgment. Check Packet Setup > APRS Outbox for the matched response. Messages are not automatically retried.
Use the displayed frequency to confirm the intended APRS channel before sending.

Native and KISS receive share the same stored inbox. Up to 500 distinct text
messages, 500 saved packets, and 2000 recent unsaved packets are retained in
`~/.config/rig-companion-zero/station.sqlite3`. Native messages are captured
as received. Older records held only by the handheld cannot be downloaded.

## Readable reports and map

Positions, compressed positions, Mic-E, objects/items and PHG site information
get readable details. Telemetry displays each channel and digital bits. When a
station sends its own PARM/UNIT/EQNS/BITS definitions, the app remembers labels
and calibration for up to 128 stations. Until calibration arrives, values are
explicitly marked raw. Telemetry setup packets are classified separately from
personal messages. Previously stored packets are decoded again on upgrade,
retaining their IDs, raw content, receive timestamps and saved flags.

Press **T** in a packet detail to switch between readable and raw content.
Press **M** from APRS, Inbox, Weather or packet detail to open the map. The map
starts near the most recent station or the opened position report. It shows
up to 128 known stations and the selected station's last 16 distinct positions.
On the map, the bottom keys are **4 Back**, **5 Zoom -**, **6 Zoom +**,
**7 Next station**, **8 Open details**. Arrow keys pan; Tab cycles stations;
**A** fits all stations and **R** centers the selected station. Existing
Save and Reply actions work from the station's packet details.

Map tiles are © OpenStreetMap contributors. Compact attribution stays in the
map's top bar. Press I on the map, or Link > Map data licences, for the data
source, ODbL licence and copyright URL. Only visible tiles are requested,
with a dedicated background worker and HTTP caching. The disk cache holds up
to 256 tiles, with at least seven-day retention unless HTTP headers specify
otherwise. Closing the map pauses further requests. Previously viewed areas
work offline; unvisited offline areas show markers without geographic detail.
Map requests disclose the viewed map area and public IP to the tile provider.
The owner authorized OpenStreetMap requests for this device on 2026-11-27.
No aprs.fi API, code or historical database is used. Sources and third-party
licenses are recorded in `THIRD-PARTY.md` and bundled with the package.

## Optional phone and PC bridges

**Link > BLE phone bridge** advertises **B.B. Link** using the BLE KISS UUIDs.
The TX characteristic accepts writes with and without response. Notifications
use 20-byte chunks compatible with the default ATT MTU. The bridge implements
the B.B. Link frequency/rate and restore extension for RadioMail. Phone
subscription starts KISS. This first version needs a live iPhone qualification.

**Link > TCP KISS / 8001** enables a single TCP client on port **8001**. Use a
trusted local network. A client connection starts KISS. Local compose, BLE and
TCP share one radio session owner. On disconnect the app restores the saved
radio state. Stop a local KISS session before connecting a phone or PC.

Before KISS, the app persists the TNC mode, VFO record, channel mode and rate.
It verifies restoration after exit. Unplugging or power loss leaves a recovery
record which is retried after the same saved radio reconnects. Errors remain
visible and radio changes are blocked until restoration succeeds.

## Build and run

Runtime dependencies are Debian's `python3-pygame`, `python3-numpy`,
`python3-dbus`, `python3-gi`, `python3-pil`, `bluez`, and `fonts-dejavu-core`. The app runs as
the logged-in user, who needs normal input, video and Bluetooth permissions.

```sh
python3 -m unittest discover -s tests -v
python3 -m rigzero --preview previews
python3 package.py
sudo dpkg -i dist/rig-companion-zero_0.3.0-1_arm64.deb
rig-companion-zero
```

The Debian package adds a **Rig Companion** launcher entry. `--status` reports
stored setup and recovery state without opening the radio. `--seconds N`
limits a launch for display testing. `--preview FOLDER` renders clearly
illustrative screens without using Bluetooth. Only one live instance can run.

This release does not include audio streaming, digital audio modems, a web
front panel, or profiles for other radio models. Package publication, account,
share code and store submission are separate release decisions. No store
submission is performed by the build script.

Sources checked 2026-11-27 include the
[BlueZ profile](https://bluez.readthedocs.io/en/latest/profile-api/),
[GATT](https://bluez.readthedocs.io/en/latest/gatt-api/) and
[advertising](https://bluez.readthedocs.io/en/latest/advertising-api/) APIs,
the [BLE KISS API](https://github.com/hessu/aprs-specs/blob/master/BLE-KISS-API.md),
and our CYD/TH-D75 hardware findings in the sibling project.


## About and licence

Link > **About Rig Companion** (or **A** inside the **?** guide) shows the
standard Nomad About screen: name, version, licence, Developed by TheTechNomad,
www.TheTechNomad.com with a QR code, and github.com/TeddyPete.

Rig Companion is part of NomadTools by TheTechNomad and is licensed under the
GNU General Public License v3.0 only (GPL-3.0-only), see `../LICENSE`.
Bundled third-party files keep their own MIT and Apache-2.0 licences, listed in
`THIRD-PARTY.md` with full texts in `licenses/`.
