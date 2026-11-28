"""Supported radios. Kenwood models are confirmed by CAT replies; Benshi models by the radio picker."""

import re

MODEL_NAMES = ("TH-D75", "TH-D74", "TM-D750")

SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
GAIA_UUID = "00001107-d102-11e1-9b23-00025b00a5a5"

# Every radio runs the same screens and keys. The family picks the driver; the
# feature set hides what a radio can't do (for example D-STAR on a Benshi radio).
RADIOS = (
    {"key": "TH-D75", "name": "Kenwood TH-D75", "family": "kenwood", "prefixes": ("TH-D75",),
     "features": {"controls", "tune", "aprs", "kiss", "packet", "dstar", "gps"}},
    {"key": "TH-D74", "name": "Kenwood TH-D74", "family": "kenwood", "prefixes": ("TH-D74",),
     "features": {"aprs", "kiss", "packet"}},
    {"key": "TM-D750", "name": "Kenwood TM-D750", "family": "kenwood", "prefixes": ("TM-D750",),
     "features": {"aprs", "kiss", "packet"}},
    {"key": "UV-Pro", "name": "BTECH UV-Pro / UV-50Pro", "family": "benshi", "prefixes": ("UV-PRO", "UV-50PRO", "UV PRO"),
     "features": {"controls", "aprs", "kiss", "packet"}},
    {"key": "GA-5WB", "name": "Radioddity GA-5WB / DB50-B", "family": "benshi", "prefixes": ("GA-5WB", "DB50"),
     "features": {"controls", "aprs", "kiss", "packet"}},
    {"key": "VR-N76", "name": "Vero VR-N76 / N75", "family": "benshi", "prefixes": ("VR-N76", "VR-N75"),
     "features": {"controls", "aprs", "kiss", "packet"}},
    {"key": "VR-N7500", "name": "Vero VR-N7500 / N7600", "family": "benshi", "prefixes": ("VR-N7500", "VR-N7600"),
     "features": {"controls", "aprs", "kiss", "packet"}},
    {"key": "RT-660", "name": "Radtel RT-660", "family": "benshi", "prefixes": ("RT-660",),
     "features": {"controls", "aprs", "kiss", "packet"}},
)
DEFAULT_RADIO = "TH-D75"


def radio_info(key):
    return next((radio for radio in RADIOS if radio["key"] == key), RADIOS[0])


def has_feature(key, feature):
    return feature in radio_info(key)["features"]


def model_from_id(line):
    match = re.fullmatch(r"ID (TH-D75|TH-D74|TM-D750)([AE])?", line)
    return match[1] if match else None


def discovery_candidate(name, uuids, family=None):
    """True when a Bluetooth device looks like a supported radio (of `family`, if given)."""
    upper = name.upper()
    uuids = {str(uuid).lower() for uuid in uuids}
    for radio in RADIOS:
        if family and radio["family"] != family:
            continue
        if any(upper.startswith(prefix) for prefix in radio["prefixes"]):
            return True
    if family in (None, "kenwood") and SPP_UUID in uuids:
        return True
    return family in (None, "benshi") and GAIA_UUID in uuids
