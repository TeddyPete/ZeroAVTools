"""Per-user settings and a bounded, transactional packet/message archive."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .aprs import describe


class Store:
    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.lock = threading.RLock()
        self.revision = 0
        self.station_cache = (-1, [])
        self.db = sqlite3.connect(folder / "station.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT NOT NULL,
                recipient TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS packets(
                id INTEGER PRIMARY KEY, time TEXT NOT NULL, source TEXT NOT NULL,
                kind TEXT NOT NULL, body TEXT NOT NULL, saved INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY, time TEXT NOT NULL, sender TEXT NOT NULL,
                recipient TEXT NOT NULL, text TEXT NOT NULL, body TEXT NOT NULL,
                UNIQUE(sender, recipient, text));
        """)
        self.db.commit()
        self._upgrade_reports()

    def _remember_definition(self, packet):
        definition = packet.get("decoded", {}).get("definition")
        if definition:
            key = "telemetry:" + packet["sender"]
            existing = self.get(key, {})
            existing.update(definition)
            self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(existing)))
            self.db.execute("DELETE FROM settings WHERE key LIKE 'telemetry:%' AND rowid NOT IN "
                            "(SELECT rowid FROM settings WHERE key LIKE 'telemetry:%' ORDER BY rowid DESC LIMIT 128)")

    def _upgrade_reports(self):
        if self.get("report_format", 0) == 2:
            return
        from .protocol import inspect_packet
        with self.lock, self.db:
            for table in ("packets", "messages"):
                for key, body in self.db.execute(f"SELECT id,body FROM {table} ORDER BY id").fetchall():
                    old = json.loads(body)
                    new = inspect_packet(old["source"], old["destination"], old["path"], old["info"], old["direction"])
                    new["time"] = old["time"]
                    self._remember_definition(new)
                    if table == "packets":
                        self.db.execute("UPDATE packets SET kind=?,body=? WHERE id=?", (new["kind"], json.dumps(new), key))
                    else:
                        self.db.execute("UPDATE messages SET body=? WHERE id=?", (json.dumps(new), key))
            self.db.execute("INSERT OR REPLACE INTO settings VALUES('report_format','2')")

    def get(self, key, default=""):
        with self.lock:
            result = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(result[0]) if result else default

    def set(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def record(self, packet):
        body = json.dumps(packet, ensure_ascii=True)
        with self.lock, self.db:
            self._remember_definition(packet)
            cursor = self.db.execute("INSERT INTO packets(time,source,kind,body) VALUES(?,?,?,?)",
                                     (packet["time"], packet["source"], packet["kind"], body))
            packet_id = cursor.lastrowid
            if packet["kind"] == "Message" and packet["direction"] == "RX":
                self.db.execute("INSERT OR IGNORE INTO messages(time,sender,recipient,text,body) VALUES(?,?,?,?,?)",
                                (packet["time"], packet["sender"], packet["recipient"], packet["text"], body))
            if packet["kind"] == "Ack" and packet["direction"] == "RX":
                token = packet["text"][3:]
                if len(token) == 5 and token.isascii() and token.isdigit():
                    self.db.execute("UPDATE outbox SET status=? WHERE id % 100000=? "
                                    "AND sender=? AND recipient=? AND status IN ('Awaiting ACK','Send uncertain') "
                                    "AND created BETWEEN datetime(?, '-24 hours') AND datetime(?)",
                                    ("Acknowledged" if packet["text"].startswith("ack") else "Rejected",
                                     int(token), packet["recipient"], packet["sender"], packet["time"], packet["time"]))
            self.db.execute("DELETE FROM packets WHERE saved=0 AND id NOT IN (SELECT id FROM packets ORDER BY id DESC LIMIT 2000)")
            self.db.execute("DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 500)")
            self.revision += 1
            return packet_id

    def save(self, packet_id):
        with self.lock, self.db:
            self.db.execute("UPDATE packets SET saved=1 WHERE id=?", (packet_id,))
            self.db.execute("UPDATE packets SET saved=0 WHERE saved=1 AND id NOT IN (SELECT id FROM packets WHERE saved=1 ORDER BY id DESC LIMIT 500)")

    def stage_message(self, sender, recipient, text, utc=None):
        with self.lock, self.db:
            row = self.db.execute("INSERT INTO outbox(sender,recipient,text,status,created) "
                                  "VALUES(?,?,?,'Send uncertain',COALESCE(datetime(?),CURRENT_TIMESTAMP))",
                                  (sender, recipient, text, utc.isoformat() if utc else None))
            self.db.execute("DELETE FROM outbox WHERE id NOT IN (SELECT id FROM outbox ORDER BY id DESC LIMIT 200)")
            return row.lastrowid

    def mark_message_sent(self, message_id):
        with self.lock, self.db:
            self.db.execute("UPDATE outbox SET status='Awaiting ACK' WHERE id=? AND status='Send uncertain'",
                            (message_id,))

    def outbox(self):
        with self.lock:
            return self.db.execute("SELECT recipient,text,status,created FROM outbox ORDER BY id DESC LIMIT 200").fetchall()

    def rows(self, view="traffic", limit=200):
        with self.lock:
            if view == "inbox":
                records = self.db.execute("SELECT id,body FROM messages ORDER BY id DESC LIMIT ?", (limit,))
            else:
                condition = {"traffic": "1", "saved": "saved=1", "weather": "kind='Weather'"}[view]
                records = self.db.execute(f"SELECT id,body FROM packets WHERE {condition} ORDER BY id DESC LIMIT ?", (limit,))
            rows = [dict(json.loads(body), id=key, view=view) for key, body in records]
            return [describe(row, self.get("telemetry:" + row.get("sender", row["source"]), {})) for row in rows]

    def stations(self):
        with self.lock:
            if self.station_cache[0] == self.revision:
                return self.station_cache[1]
            seen, stations, by_key = set(), [], {}
            for key, body in self.db.execute("SELECT id,body FROM packets ORDER BY id DESC LIMIT 2500"):
                packet = json.loads(body)
                data = packet.get("decoded", {})
                if "lat" not in data:
                    continue
                identity = packet.get("sender", packet["source"]) + "/" + data.get("name", "")
                if identity in seen:
                    previous = by_key.get(identity)
                    if previous and len(previous["trail"]) < 16:
                        point = [data["lat"], data["lon"]]
                        if previous["trail"][-1] != point:
                            previous["trail"].append(point)
                    continue
                seen.add(identity)
                if data.get("killed") or len(stations) >= 128:
                    continue
                station = describe(dict(packet, id=key, view="traffic", station_key=identity,
                                        trail=[[data["lat"], data["lon"]]]))
                stations.append(station)
                by_key[identity] = station
            self.station_cache = (self.revision, stations)
            return stations

    def close(self):
        with self.lock:
            self.db.close()

