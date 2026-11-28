"""Export the local archive. PCAP contains bytes captured from KISS only."""

import csv
import os
import struct
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def export(rows, folder):
    fields = ("time", "direction", "source", "destination", "path", "kind", "info",
              "packet_band", "frequency_hz", "packet_rate", "ax25_hex")
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-")
    out = Path(tempfile.mkdtemp(prefix=stamp, dir=folder))
    records = list(reversed(rows))
    count = 0
    with (out / "packets.csv").open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fields, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            # Prevent spreadsheet programs from interpreting station-supplied text as a formula.
            writer.writerow({key: "'" + value if isinstance(value, str) and
                             value.lstrip()[:1] in ("=", "+", "-", "@", "\t", "\r", "\n") else value
                             for key, value in row.items() if key in fields})
    with (out / "kiss.pcap").open("wb") as pcap:
        os.chmod(pcap.name, 0o600)
        pcap.write(struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 1024, 3))
        for row in records:
            if not row.get("ax25_hex"):
                continue
            raw = bytes.fromhex(row["ax25_hex"])
            when = datetime.fromisoformat(row["time"])
            pcap.write(struct.pack("<IIII", int(when.timestamp()), when.microsecond, len(raw), len(raw)))
            pcap.write(raw)
            count += 1
    return out, len(records), count
