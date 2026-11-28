"""Radio model helpers. The radios themselves come from installed radio packages (see drivers.py)."""

import re

MODEL_NAMES = ("TH-D75", "TH-D74", "TM-D750")       # Kenwood models with Field Manual entries

SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
GAIA_UUID = "00001107-d102-11e1-9b23-00025b00a5a5"
SERVICES = {"spp": SPP_UUID, "gaia": GAIA_UUID}


def model_from_id(line):
    match = re.fullmatch(r"ID (TH-D75|TH-D74|TM-D750)([AE])?", line)
    return match[1] if match else None


def radio_info(package, key=None):
    """The radio entry for `key` in a package (its first radio when unknown)."""
    radios = (package or {}).get("radios") or [{"key": key or "Radio", "name": key or "Radio",
                                                "features": set(), "prefixes": ()}]
    return next((radio for radio in radios if radio["key"] == key), radios[0])


def discovery_candidate(name, uuids, package=None):
    """True when a Bluetooth device looks like a radio of `package` (any radio when None)."""
    upper = name.upper()
    uuids = {str(uuid).lower() for uuid in uuids}
    if package is None:
        return (any(upper.startswith(model) for model in MODEL_NAMES)
                or SPP_UUID in uuids or GAIA_UUID in uuids)
    if any(upper.startswith(prefix.upper()) for radio in package["radios"] for prefix in radio["prefixes"]):
        return True
    return SERVICES.get(package.get("bluetooth_service", "spp"), SPP_UUID) in uuids
