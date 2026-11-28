"""Offline menu index and original operating notes for the TH-D75A/E."""

import json
from pathlib import Path

from .models import MODEL_NAMES

SOURCE = "Kenwood TH-D75A/E User Manual, chapters 5, 6, 12, 14 and 15."
MENUS = json.loads(Path(__file__).with_name("menus.json").read_text(encoding="utf-8"))

NOTES = {
    "102": "USB audio choices are AF, IF and Detect. Select Band B and single-band operation first.",
    "400": "Turn the radio's internal GPS receiver on or off.",
    "401": "Choose GPS or one of five stored positions. Check the source before beaconing.",
    "500": "Set the handheld's own APRS callsign and SSID. The companion's KISS callsign is configured separately.",
    "505": "Choose 1200 or 9600 bps to match the target packet channel.",
    "506": "Select the radio's data band. The app can also choose a band for its own KISS sessions.",
    "510": "Native methods are Manual, PTT, Auto and SmartBeaconing. BCN behavior depends on the method. Stop recurring beaconing before editing a live setup.",
    "511": "Initial interval for native beaconing. SmartBeaconing uses separate timing controls.",
    "530": "Set the low and high speed thresholds for SmartBeaconing. Menus 531–535 control rates and turns.",
    "590": "For native APRS monitoring on the Zero, choose Raw Packets. Route output with menu 982.",
    "930": "Enable the radio's Bluetooth. Use 934 to initiate pairing.",
    "934": "Open pairing mode, then select the TH-D75 on the Zero and compare the codes.",
    "940": "Assign the radio's PF1 key. Menu 941 assigns PF2. Menus 942–944 cover supported microphone keys.",
    "941": "Assign the radio's PF2 key. Menu 940 assigns PF1.",
    "980": "Choose COM plus audio output for radio control/audio, or mass storage to access the SD card.",
    "982": "Route native APRS output to USB or Bluetooth. On Bluetooth use 590 Raw Packets for the Zero.",
    "983": "Choose USB or Bluetooth for KISS. Select Bluetooth for this companion.",
}

KEYS = (
    ("A/B and DUAL", "Tap A/B to select the operating band. F then A/B switches single/dual band."),
    ("MENU and power", "Tap MENU for settings. F then MENU selects RF power."),
    ("F and key lock", "Tap F for the alternate functions. Hold F to toggle key lock."),
    ("VFO / scan", "Tap VFO for frequency mode. Hold VFO for band scan. F then VFO copies the selected memory/call channel to VFO."),
    ("MR / memory", "Tap MR for memory mode. F then MR opens memory registration."),
    ("CALL", "Tap CALL for the call channel. F then CALL opens call channel registration."),
    ("MSG", "Tap MSG for the message list. F then MSG opens a new APRS message."),
    ("LIST and KISS", "Tap LIST for the APRS station list. F then LIST changes the data mode."),
    ("BCN", "In APRS mode BCN sends once in Manual, or enables/disables the selected recurring/PTT behavior. F then BCN opens the APRS object function."),
    ("REV and shift", "Tap REV for reverse. F then REV selects repeater shift."),
    ("TONE and tone frequency", "Tap TONE to change signaling type. F then TONE selects the signaling frequency or code."),
    ("PF1 / attenuator", "PF1 uses your menu 940 assignment. F then PF1 toggles the attenuator where supported."),
    ("PF2 / step", "PF2 uses your menu 941 assignment. F then PF2 selects the frequency step."),
    ("MHz and Fine", "MHz selects MHz tuning. Hold it for MHz scan where available. F then MHz opens Fine on Band B in supported modes."),
    ("MARK and position", "MARK registers a position. Use F then MARK for the position display."),
    ("MONI and squelch", "MONI opens the squelch for monitoring. F then MONI opens squelch adjustment."),
    ("MODE and digital menu", "MODE selects the operating mode. In DV/DR, F then MODE opens digital functions."),
)

TIPS = (
    ("Pair over Bluetooth", "Enable menu 930, open 934, then choose Find TH-D75 under Link on the Zero. Compare and accept the pairing numbers on both devices."),
    ("APRS paired but no packets", "For native APRS enable APRS on the radio, select Raw Packets in 590 and Bluetooth in 982. Check the data band and frequency. For KISS use Bluetooth in 983."),
    ("Switch between APRS and KISS", "Use Packet on the Zero to start or end KISS. CAT updates pause during KISS. Wait for the restored confirmation before changing the radio or starting another client."),
    ("Beacon once on the handheld", "Set method 510 to Manual, confirm the callsign, position and data frequency, then press BCN in APRS mode. Check the radio's TX indication."),
    ("Native automatic beaconing", "Check menus 500, 401, 505 and 506. Select Auto or SmartBeaconing in 510. Configure 511 for Auto or 530–535 for SmartBeaconing. BCN enables or disables the selected behavior."),
    ("Save a repeater safely", "Set receive frequency, shift and tone in VFO. Use F then MR to register. Choose an unused channel and read back the saved memory before leaving setup."),
    ("USB control but no receive audio", "Use 980 COM plus audio. Choose Band B, single band and menu 102 AF. Select the Kenwood audio input on the connected computer."),
    ("Choose a PF shortcut", "Use 940 and 941 to assign PF1 and PF2. Consider different functions for field use, such as battery level and GPS. Check the manual for the full list."),
)


OTHER_MODELS = {
    "TH-D74": (
        "Kenwood TH-D74A/E User Manual, chapters 6, 12, 14 and 15.",
        (("580 APRS output", "Select Raw Packets for native APRS output."),
         ("982 APRS transport", "Choose the native APRS output connection."),
         ("983 KISS transport", "Select USB or Bluetooth for KISS."),
         ("505 Packet speed", "Select 1200 or 9600 bps."),
         ("506 Data band", "Choose the data band A or B.")),
        (("F then LIST", "Use this function to enter KISS on the handheld."),),
        (("Monitor in KISS", "Set menu 983 to Bluetooth. Enter KISS on the radio, then choose Handset KISS monitor under Packet in the app."),),
    ),
    "TM-D750": (
        "Kenwood TM-D750A/E User Manual, APRS, KISS and Bluetooth chapters.",
        (("505 Packet speed", "Set 1200 or 9600 bps to match the packet channel."),
         ("509 TX delay", "Set the TNC delay before it begins sending data."),
         ("934 Bluetooth pairing", "Open pairing mode on the control panel."),
         ("984 KISS transport", "Select Bluetooth, main-unit USB, or panel USB.")),
        (("KEY then APRS then APRS", "Use KEY for APRS soft keys, then select APRS twice to enter KISS."),),
        (("Monitor in KISS", "Set menu 984 to Bluetooth. Enter KISS on the radio, then choose Handset KISS monitor under Packet in the app."),),
    ),
}


def entries(section, query="", model=MODEL_NAMES[0]):
    if model in OTHER_MODELS:
        source, *sections = OTHER_MODELS[model]
        items = [(title, detail + f"\n\n{source}") for title, detail in sections[section]]
    elif section == 0:
        items = [
            (f"{number}  {title}", f"On the radio: MENU → {number}.\n"
             + NOTES.get(number, "See the matching entry in chapter 6 for available settings.")
             + f"\n\n{SOURCE}")
            for number, title in MENUS.items()
        ]
    else:
        items = [(title, detail + f"\n\n{SOURCE}") for title, detail in (KEYS if section == 1 else TIPS)]
    words = query.casefold().split()
    matches = [entry for entry in items if all(word in " ".join(entry).casefold() for word in words)]
    return sorted(matches, key=lambda entry: not all(word in entry[0].casefold() for word in words))
