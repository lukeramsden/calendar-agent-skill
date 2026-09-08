"""Transactional cache and source-revision-aware coverage; network work precedes commits."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from dateutil.relativedelta import relativedelta
from icalendar import Calendar

from .documents import (CalendarError, UTC, KINDS, parse, digest, identity, describe,
                        container_keys, expand, iso, parse_bound, MAX_BYTES)
from .sources import Secrets, Transport, DAVClient, DAV, private_dir

ENGINE = "recurring-ical-events:3.8.2/schema:1"
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
 timezone TEXT NOT NULL, past_years INTEGER NOT NULL, future_years INTEGER NOT NULL,
 fetched_at TEXT, etag TEXT, modified TEXT, last_error TEXT);
CREATE TABLE IF NOT EXISTS feeds (source_id TEXT PRIMARY KEY REFERENCES sources(id), raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS feed_snapshots (
 source_id TEXT NOT NULL REFERENCES sources(id), revision TEXT NOT NULL, raw BLOB NOT NULL,
 observed_at TEXT NOT NULL, PRIMARY KEY(source_id,revision));
CREATE TABLE IF NOT EXISTS resource_history (
 calendar_id TEXT NOT NULL REFERENCES calendars(id), href TEXT NOT NULL, etag TEXT NOT NULL, raw BLOB NOT NULL,
 observed_at TEXT NOT NULL, PRIMARY KEY(calendar_id,href,etag));
CREATE TABLE IF NOT EXISTS calendars (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), source_key TEXT NOT NULL,
 name TEXT NOT NULL, revision TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 metadata TEXT NOT NULL DEFAULT '{}', sync_token TEXT,
 UNIQUE(source_id,source_key));
CREATE TABLE IF NOT EXISTS snapshots (
 calendar_id TEXT NOT NULL REFERENCES calendars(id), revision TEXT NOT NULL, raw BLOB NOT NULL,
 observed_at TEXT NOT NULL, PRIMARY KEY(calendar_id,revision));
CREATE TABLE IF NOT EXISTS components (
 id INTEGER PRIMARY KEY, calendar_id TEXT NOT NULL REFERENCES calendars(id), revision TEXT NOT NULL,
 kind TEXT NOT NULL, uid TEXT NOT NULL, rid TEXT NOT NULL, raw BLOB NOT NULL,
 summary TEXT NOT NULL, description TEXT NOT NULL, location TEXT NOT NULL, people TEXT NOT NULL,
 UNIQUE(calendar_id,revision,kind,uid,rid));
CREATE VIRTUAL TABLE IF NOT EXISTS component_fts USING fts5(summary,description,location,people,uid);
CREATE TABLE IF NOT EXISTS resources (
 calendar_id TEXT NOT NULL REFERENCES calendars(id), href TEXT NOT NULL, etag TEXT NOT NULL, raw BLOB NOT NULL,
 PRIMARY KEY(calendar_id,href));
CREATE TABLE IF NOT EXISTS occurrences (
 calendar_id TEXT NOT NULL REFERENCES calendars(id), revision TEXT NOT NULL, timezone TEXT NOT NULL,
 engine TEXT NOT NULL, kind TEXT NOT NULL, uid TEXT NOT NULL, rid TEXT NOT NULL,
 start TEXT NOT NULL, end TEXT NOT NULL, all_day INTEGER NOT NULL, summary TEXT NOT NULL,
 status TEXT NOT NULL, transparent INTEGER NOT NULL, raw TEXT NOT NULL,
 UNIQUE(calendar_id,revision,timezone,engine,kind,uid,rid,start));
CREATE INDEX IF NOT EXISTS occurrence_time ON occurrences(calendar_id,revision,start,end);
CREATE TABLE IF NOT EXISTS coverage (
 calendar_id TEXT NOT NULL REFERENCES calendars(id), revision TEXT NOT NULL, timezone TEXT NOT NULL,
 engine TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, completed_at TEXT NOT NULL,
 PRIMARY KEY(calendar_id,revision,timezone,engine,start,end));
CREATE TABLE IF NOT EXISTS expansion_attempts (
 id TEXT PRIMARY KEY, calendar_id TEXT NOT NULL, revision TEXT NOT NULL, timezone TEXT NOT NULL,
 start TEXT NOT NULL, end TEXT NOT NULL, status TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS sync_runs (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS drafts (
 id TEXT PRIMARY KEY, calendar_id TEXT, base_revision TEXT, raw BLOB NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mutation_history (
 id INTEGER PRIMARY KEY, draft_id TEXT NOT NULL, raw BLOB NOT NULL, changed_at TEXT NOT NULL);
"""


def now():
    return iso(datetime.now(UTC))


def holes(start, end, intervals):
    """Subtract completed half-open intervals, tolerating overlap and duplicates."""
    cursor = start
    missing = []
    for a, b in sorted(intervals):
        if b <= cursor or a >= end:
            continue
        if a > cursor:
            missing.append((cursor, min(a, end)))
        cursor = max(cursor, b)
        if cursor >= end:
            break
    if cursor < end:
        missing.append((cursor, end))
    return missing


class Store:
    def __init__(self, directory=None):
        os.umask(0o077)
        self.directory = private_dir(directory or os.environ.get("CALENDAR_DATA") or
            Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "calendar-agent-skill")
        path = self.directory / "calendar.db"
        if path.is_symlink():
            raise CalendarError("Cache database must not be a symbolic link")
        self.db = sqlite3.connect(path, timeout=20)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise CalendarError("Unsupported cache schema; use the matching CLI version")
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA user_version=1")
        self.secrets = Secrets(self.directory / "credentials")

    def rows(self, sql, args=()):
        return [dict(row) for row in self.db.execute(sql, args)]

    def source(self, name):
        result = self.rows("SELECT * FROM sources WHERE id=? OR name=?", (name, name))
        if not result:
            raise CalendarError("Unknown source")
        return result[0]

    def calendar(self, ident):
        result = self.rows("SELECT * FROM calendars WHERE id=?", (ident,))
        if not result:
            raise CalendarError("Unknown calendar ID")
        return result[0]

    def add_source(self, name, kind, config, tz="UTC", past_years=5, future_years=1):
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
        if kind not in ("feed", "file", "caldav") or min(past_years, future_years) < 0:
            raise CalendarError("Invalid source kind or backfill window")
        if kind != "file":
            Transport(config)
        else:
            config["url"] = str(Path(config["url"]).expanduser().resolve())
        if self.rows("SELECT id FROM sources WHERE name=?", (name,)):
            raise CalendarError("Source name already exists")
        ident = uuid.uuid4().hex
        self.secrets.set(ident, config)
        with self.db:
            self.db.execute("INSERT INTO sources(id,name,kind,timezone,past_years,future_years) VALUES(?,?,?,?,?,?)",
                            (ident, name, kind, tz, past_years, future_years))
        return {"id": ident, "name": name, "kind": kind, "timezone": tz}

    def _snapshot(self, source, key, calendar, metadata=None):
        raw = calendar.to_ical()
        revision = digest(raw)
        existing = self.rows("SELECT * FROM calendars WHERE source_id=? AND source_key=?", (source["id"], key))
        cid = existing[0]["id"] if existing else uuid.uuid4().hex
        name = str(calendar.get("X-WR-CALNAME", calendar.get("NAME", source["name"])))
        if metadata and metadata.get("name"):
            name = metadata["name"]
        self.db.execute("""INSERT INTO calendars(id,source_id,source_key,name,revision,metadata)
            VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,source_key) DO UPDATE SET
            name=excluded.name,revision=excluded.revision,active=1,metadata=excluded.metadata""",
            (cid, source["id"], key, name, revision, json.dumps(metadata or {})))
        inserted = self.db.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)", (cid, revision, raw, now())).rowcount
        if inserted:
            for c in calendar.subcomponents:
                if c.name not in KINDS:
                    continue
                kind, uid, rid = identity(c)
                values = [str(c.get(p, "")) for p in ("SUMMARY", "DESCRIPTION", "LOCATION")]
                people = str(c.get("ORGANIZER", "")) + " " + str(c.get("ATTENDEE", ""))
                cursor = self.db.execute("""INSERT INTO components(calendar_id,revision,kind,uid,rid,raw,
                    summary,description,location,people) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (cid, revision, kind, uid, rid, c.to_ical(), *values, people))
                self.db.execute("INSERT INTO component_fts(rowid,summary,description,location,people,uid) VALUES(?,?,?,?,?,?)",
                                (cursor.lastrowid, *values, people, uid))
        return cid

    def raw(self, calendar_id, revision=None):
        c = self.calendar(calendar_id)
        row = self.db.execute("SELECT raw FROM snapshots WHERE calendar_id=? AND revision=?",
                              (calendar_id, revision or c["revision"])).fetchone()
        if row is None:
            raise CalendarError("Unknown calendar revision")
        return row[0]

    def _fetch_feed(self, source, config):
        if source["kind"] == "file":
            try:
                with open(config["url"], "rb") as f:
                    raw = f.read(MAX_BYTES + 1)
            except OSError:
                raise CalendarError("Unable to read local source file") from None
            headers = {}
        else:
            status, headers, raw, _ = Transport(config).feed(source["etag"], source["modified"])
            if status == 304:
                cached = self.db.execute("SELECT raw FROM feeds WHERE source_id=?", (source["id"],)).fetchone()
                if cached is None:
                    raise CalendarError("Server returned 304 without a cached snapshot")
                raw = cached[0]
                headers.setdefault("ETag", source["etag"])
                headers.setdefault("Last-Modified", source["modified"])
        calendars = parse(raw)
        keys = container_keys(calendars)
        with self.db:
            self.db.execute("UPDATE calendars SET active=0 WHERE source_id=?", (source["id"],))
            for key, calendar in zip(keys, calendars):
                self._snapshot(source, key, calendar)
            self.db.execute("INSERT OR IGNORE INTO feed_snapshots VALUES(?,?,?,?)",
                            (source["id"], digest(raw), raw, now()))
            self.db.execute("INSERT INTO feeds VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET raw=excluded.raw",
                            (source["id"], raw))
            self.db.execute("UPDATE sources SET etag=?,modified=?,fetched_at=?,last_error=NULL WHERE id=?",
                            (headers.get("ETag"), headers.get("Last-Modified"), now(), source["id"]))

    def _fetch_dav(self, source, config):
        dav = DAVClient(config)
        discovered = dav.discover()
        seen = []
        for remote in discovered:
            key = remote["href"]
            old = self.rows("SELECT * FROM calendars WHERE source_id=? AND source_key=?", (source["id"], key))
            cid = old[0]["id"] if old else None
            previous = {r["href"]: r for r in self.rows("SELECT * FROM resources WHERE calendar_id=?", (cid,))} if cid else {}
            rows, token, full = dav.listing(key, old[0]["sync_token"] if old else None)
            current = {} if full else dict(previous)
            for row in rows:
                href = row["href"]
                if " 404 " in row["status"]:
                    current.pop(href, None)
                    continue
                etag = row["properties"]["{" + DAV + "}getetag"].text
                if href in previous and previous[href]["etag"] == etag:
                    current[href] = previous[href]
                else:
                    raw, actual_etag = dav.get(href)
                    if actual_etag != etag:
                        raise CalendarError("Calendar changed during sync; retry to obtain a consistent snapshot")
                    current[href] = {"href": href, "raw": raw, "etag": actual_etag}
            combined = Calendar()
            combined.add("VERSION", "2.0")
            combined.add("PRODID", "-//calendar-agent-skill//EN")
            timezones = {}
            for resource in sorted(current.values(), key=lambda r: r["href"]):
                calendars = parse(resource["raw"])
                if len(calendars) != 1:
                    raise CalendarError("CalDAV resource must contain one VCALENDAR")
                for component in calendars[0].subcomponents:
                    if component.name == "VTIMEZONE":
                        tzid = str(component.get("TZID", ""))
                        if tzid in timezones:
                            if timezones[tzid] != component.to_ical():
                                raise CalendarError("Conflicting VTIMEZONE definitions in calendar")
                            continue
                        timezones[tzid] = component.to_ical()
                    combined.add_component(component)
            parse(combined.to_ical())
            with self.db:
                cid = self._snapshot(source, key, combined, remote)
                self.db.execute("DELETE FROM resources WHERE calendar_id=?", (cid,))
                self.db.executemany("INSERT INTO resources VALUES(?,?,?,?)",
                    [(cid, r["href"], r["etag"], r["raw"]) for r in current.values()])
                self.db.executemany("INSERT OR IGNORE INTO resource_history VALUES(?,?,?,?,?)",
                    [(cid, r["href"], r["etag"], r["raw"], now()) for r in current.values()])
                self.db.execute("UPDATE calendars SET sync_token=? WHERE id=?", (token, cid))
            seen.append(cid)
        with self.db:
            self.db.execute("UPDATE calendars SET active=0 WHERE source_id=?", (source["id"],))
            self.db.executemany("UPDATE calendars SET active=1 WHERE id=?", [(c,) for c in seen])
            self.db.execute("UPDATE sources SET fetched_at=?,last_error=NULL WHERE id=?", (now(), source["id"]))

    def sync(self, name, start=None, end=None, fetch=True):
        import fcntl
        source = self.source(name)
        lock_dir = private_dir(self.directory / "locks")
        with open(lock_dir / source["id"], "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CalendarError("Source sync already running; retry after it completes") from None
            try:
                return self._sync_locked(source, start, end, fetch)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _sync_locked(self, source, start, end, fetch):
        timestamp = datetime.now(UTC)
        start = start or timestamp - relativedelta(years=source["past_years"])
        end = end or timestamp + relativedelta(years=source["future_years"])
        if start >= end:
            raise CalendarError("Window start must precede end")
        run_id = uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO sync_runs(id,source_id,started_at,status) VALUES(?,?,?,?)",
                            (run_id, source["id"], now(), "running"))
        try:
            if fetch:
                config = self.secrets.get(source["id"])
                if source["kind"] == "caldav":
                    self._fetch_dav(source, config)
                else:
                    self._fetch_feed(source, config)
            calendars = self.rows("SELECT * FROM calendars WHERE source_id=? AND active=1", (source["id"],))
            for c in calendars:
                self.ensure_window(c["id"], start, end)
            with self.db:
                self.db.execute("UPDATE sync_runs SET status='complete',finished_at=? WHERE id=?", (now(), run_id))
            return {"run_id": run_id, "calendars": [c["id"] for c in calendars], "start": iso(start), "end": iso(end)}
        except Exception:
            with self.db:
                self.db.execute("UPDATE sync_runs SET status='failed',finished_at=?,error=? WHERE id=?",
                                (now(), "Sync failed; completed chunks retained", run_id))
                self.db.execute("UPDATE sources SET last_error=? WHERE id=?", ("Sync failed; cache may be stale", source["id"]))
            raise

    def coverage(self, cid, start=None, end=None):
        c = self.calendar(cid)
        source = self.source(c["source_id"])
        ranges = self.rows("""SELECT start,end,completed_at FROM coverage WHERE calendar_id=? AND revision=?
            AND timezone=? AND engine=? ORDER BY start""", (cid, c["revision"], source["timezone"], ENGINE))
        result = {"calendar_id": cid, "revision": c["revision"], "timezone": source["timezone"],
                  "engine": ENGINE, "intervals": ranges, "upstream_history_complete": "unknown",
                  "incomplete_attempts": self.rows("""SELECT start,end,status FROM expansion_attempts
                      WHERE calendar_id=? AND revision=? AND timezone=? AND status!='complete'""",
                      (cid, c["revision"], source["timezone"]))}
        if start is not None and end is not None:
            result["gaps"] = [{"start": iso(a), "end": iso(b)} for a, b in holes(start, end,
                [(parse_bound(r["start"]), parse_bound(r["end"])) for r in ranges])]
        return result

    def ensure_window(self, cid, start, end):
        c = self.calendar(cid)
        tz = self.source(c["source_id"])["timezone"]
        raw = self.raw(cid)
        for gap in self.coverage(cid, start, end)["gaps"]:
            cursor, stop = parse_bound(gap["start"]), parse_bound(gap["end"])
            while cursor < stop:
                chunk_end = min(cursor + timedelta(days=31), stop)
                attempt = uuid.uuid4().hex
                with self.db:
                    self.db.execute("INSERT INTO expansion_attempts VALUES(?,?,?,?,?,?,?,NULL)",
                        (attempt, cid, c["revision"], tz, iso(cursor), iso(chunk_end), "running"))
                try:
                    occurrences = expand(raw, cursor, chunk_end, tz)
                except BaseException:
                    with self.db:
                        self.db.execute("UPDATE expansion_attempts SET status='failed',finished_at=? WHERE id=?",
                                        (now(), attempt))
                    raise
                with self.db:
                    if self.calendar(cid)["revision"] != c["revision"]:
                        raise CalendarError("Source changed during expansion; retry")
                    for o in occurrences:
                        self.db.execute("""INSERT OR IGNORE INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (cid, c["revision"], tz, ENGINE, o["kind"], o["uid"], o["recurrence_id"],
                             o["start"], o["end"], o["all_day"], o["summary"], o["status"], o["transparent"], o["raw"]))
                    self.db.execute("INSERT OR IGNORE INTO coverage VALUES(?,?,?,?,?,?,?)",
                                    (cid, c["revision"], tz, ENGINE, iso(cursor), iso(chunk_end), now()))
                    self.db.execute("UPDATE expansion_attempts SET status='complete',finished_at=? WHERE id=?",
                                    (now(), attempt))
                cursor = chunk_end

    def search(self, query=None, calendar_id=None, kind=None, limit=100, offset=0, history=False):
        if not 1 <= limit <= 10000 or offset < 0:
            raise CalendarError("limit must be 1..10000 and offset nonnegative")
        params = []
        sql = "SELECT c.id,c.calendar_id,c.revision,c.kind,c.uid,c.rid,c.summary,c.location"
        if query:
            sql += ",bm25(component_fts) AS rank,snippet(component_fts,1,'[',']','…',24) AS snippet"
        sql += " FROM components c JOIN calendars a ON a.id=c.calendar_id"
        if query:
            sql += " JOIN component_fts ON component_fts.rowid=c.id"
        conditions = []
        if not history:
            conditions.extend(["c.revision=a.revision", "a.active=1"])
        if query:
            conditions.append("component_fts MATCH ?")
            params.append(query)
        if calendar_id:
            conditions.append("c.calendar_id=?")
            params.append(calendar_id)
        if kind:
            conditions.append("c.kind=?")
            params.append(kind)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY " + ("rank,c.id" if query else "c.id") + " LIMIT ? OFFSET ?"
        try:
            return self.rows(sql, (*params, limit, offset))
        except sqlite3.OperationalError:
            raise CalendarError("Invalid full-text search expression") from None

    def read(self, ident):
        rows = self.rows("SELECT * FROM components WHERE id=?", (ident,))
        if not rows:
            raise CalendarError("Unknown component ID")
        row = rows[0]
        raw = row.pop("raw")
        row["raw"] = raw.decode()
        from icalendar import Component
        row["component"] = describe(Component.from_ical(raw))
        return row

    def agenda(self, start, end, calendar_id=None):
        if start >= end:
            raise CalendarError("Window start must precede end")
        calendars = self.rows("SELECT * FROM calendars WHERE active=1" + (" AND id=?" if calendar_id else ""),
                              (calendar_id,) if calendar_id else ())
        coverage = [self.coverage(c["id"], start, end) for c in calendars]
        sql = """SELECT o.* FROM occurrences o JOIN calendars c ON c.id=o.calendar_id
            JOIN sources s ON s.id=c.source_id WHERE c.active=1 AND o.revision=c.revision
            AND o.timezone=s.timezone AND o.engine=? AND o.start<? AND (o.end>? OR (o.end=o.start AND o.start>=?))"""
        args = [ENGINE, iso(end), iso(start), iso(start)]
        if calendar_id:
            sql += " AND c.id=?"
            args.append(calendar_id)
        rows = self.rows(sql + " ORDER BY o.start,o.calendar_id,o.uid LIMIT 100001", args)
        if len(rows) > 100000:
            raise CalendarError("Agenda exceeds 100,000 occurrences; narrow the date range or calendar")
        for row in rows:
            row.pop("raw")
        return {"occurrences": rows, "coverage": coverage, "complete": bool(calendars) and all(not c["gaps"] for c in coverage)}

    def status(self):
        return {"sources": self.rows("SELECT * FROM sources"),
                "calendars": self.rows("SELECT id,source_id,name,revision,active FROM calendars"),
                "recent_runs": self.rows("SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 20"),
                "database_bytes": (self.directory / "calendar.db").stat().st_size,
                "note": "Coverage certifies available-source processing, not upstream historical completeness"}

    def draft(self, raw, calendar_id=None, ident=None):
        parse(raw)
        if ident:
            existing = self.rows("SELECT * FROM drafts WHERE id=?", (ident,))
            if not existing:
                raise CalendarError("Unknown draft")
            with self.db:
                self.db.execute("INSERT INTO mutation_history(draft_id,raw,changed_at) VALUES(?,?,?)",
                                (ident, existing[0]["raw"], now()))
                self.db.execute("UPDATE drafts SET raw=? WHERE id=?", (raw, ident))
        else:
            ident = uuid.uuid4().hex
            revision = self.calendar(calendar_id)["revision"] if calendar_id else None
            with self.db:
                self.db.execute("INSERT INTO drafts VALUES(?,?,?,?,?)", (ident, calendar_id, revision, raw, now()))
        return {"draft_id": ident, "remote_modified": False}

    def draft_raw(self, ident):
        row = self.db.execute("SELECT raw FROM drafts WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise CalendarError("Unknown draft")
        return row[0]
