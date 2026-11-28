"""Nearby reflectors: where each reflector's server is, and how far it is from you.

Reflector lists only carry addresses, so the server address is looked up once
with the free ip-api.com batch service (no key; 100 addresses per request, 15
requests a minute) and cached for 30 days. This is the server's location, which
is usually, but not always, near the reflector's owner (some run on rented servers).
"""
from __future__ import annotations

import ipaddress
import json
import math
import re
import socket
import threading
import time
import urllib.request
from pathlib import Path

BATCH_URL = "http://ip-api.com/batch?fields=status,lat,lon,city,regionName,countryCode,query"
CACHE_DAYS = 30
BATCH = 100
PAUSE = 4.5                 # stay under 15 requests a minute


def grid_to_latlon(grid: str):
    """Maidenhead locator (4, 6 or 8 characters) -> centre (lat, lon), or None."""
    grid = (grid or "").strip().upper()
    if not re.fullmatch(r"[A-R]{2}[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?", grid):
        return None
    lon = (ord(grid[0]) - 65) * 20 - 180
    lat = (ord(grid[1]) - 65) * 10 - 90
    lon += int(grid[2]) * 2
    lat += int(grid[3])
    lon_size, lat_size = 2.0, 1.0
    if len(grid) >= 6:
        lon_size, lat_size = lon_size / 24, lat_size / 24
        lon += (ord(grid[4]) - 65) * lon_size
        lat += (ord(grid[5]) - 65) * lat_size
    if len(grid) == 8:
        lon_size, lat_size = lon_size / 10, lat_size / 10
        lon += int(grid[6]) * lon_size
        lat += int(grid[7]) * lat_size
    return lat + lat_size / 2, lon + lon_size / 2


def parse_latlon(text: str):
    """'44.98,-93.27' -> (44.98, -93.27), or None."""
    try:
        lat, lon = (float(part) for part in str(text).split(","))
    except (TypeError, ValueError):
        return None
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def distance_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(h)))


def is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def public_ip(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


class Nearby:
    def __init__(self, folder: Path, opener=None, sleep=time.sleep):
        self.path = Path(folder) / "reflector-locations.json"
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep
        self.places = {}            # ip -> [lat, lon, place, fetched]
        self.names = {}             # hostname -> ip
        self.busy = False
        self.done = self.total = 0
        self.error = ""
        self.lock = threading.Lock()
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text())
            self.places, self.names = data.get("places", {}), data.get("names", {})
        except (OSError, ValueError):
            self.places, self.names = {}, {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".part")
        temp.write_text(json.dumps({"places": self.places, "names": self.names}))
        temp.replace(self.path)

    def address_of(self, host: str):
        host = host.strip()
        if public_ip(host):
            return host
        return self.names.get(host.lower())

    def missing(self, table):
        """Addresses in the reflector table that have no fresh location yet."""
        cutoff = time.time() - CACHE_DAYS * 86400
        wanted, hosts = set(), set()
        for entry in table.values():
            host = str(entry[0]).strip()
            address = self.address_of(host)
            if address is None:
                if host and not is_ip(host) and re.fullmatch(r"[A-Za-z0-9.-]+", host):
                    hosts.add(host.lower())
                continue
            place = self.places.get(address)
            if not place or place[3] < cutoff:
                wanted.add(address)
        return wanted, hosts

    def ready(self, table):
        wanted, hosts = self.missing(table)
        return not wanted and not hosts

    def refresh(self, table, resolve=socket.gethostbyname):
        """Look up every reflector server not in the cache. Slow the first time (about 2 minutes)."""
        with self.lock:
            if self.busy:
                return
            self.busy = True
        try:
            self.error = ""
            wanted, hosts = self.missing(table)
            self.total, self.done = len(wanted) + len(hosts), 0
            for host in sorted(hosts):
                try:
                    address = resolve(host)
                    self.names[host] = address
                    if public_ip(address) and address not in self.places:
                        wanted.add(address)
                except OSError:
                    self.names[host] = ""
                self.done += 1
            addresses = sorted(wanted)
            self.total = self.done + len(addresses)
            for start in range(0, len(addresses), BATCH):
                if start:
                    self.sleep(PAUSE)
                chunk = addresses[start:start + BATCH]
                request = urllib.request.Request(BATCH_URL, data=json.dumps(chunk).encode(),
                                                 headers={"Content-Type": "application/json"})
                with self.opener(request, timeout=20) as response:
                    results = json.loads(response.read(400_000))
                now = time.time()
                for item in results:
                    if item.get("status") == "success":
                        city = item.get("city") or ""
                        region = item.get("regionName") or ""
                        country = item.get("countryCode") or ""
                        place = ", ".join(x for x in (city, region if country == "US" else country) if x)
                        self.places[item["query"]] = [item["lat"], item["lon"], place, now]
                    else:
                        self.places[item.get("query", "")] = [None, None, "", now]
                self.done += len(chunk)
                self.save()
            self.save()
        except Exception as error:
            self.error = "Location lookup stopped: " + str(error)[:60]
            self.save()
        finally:
            self.busy = False

    def closest(self, table, origin, prefix="", limit=80):
        """[(name, km, place)] nearest first."""
        rows = []
        for name, entry in table.items():
            if prefix and not name.startswith(prefix):
                continue
            address = self.address_of(str(entry[0]))
            place = self.places.get(address) if address else None
            if not place or place[0] is None:
                continue
            rows.append((name, distance_km(origin, (place[0], place[1])), place[2]))
        rows.sort(key=lambda row: (row[1], row[0]))
        return rows[:limit]
