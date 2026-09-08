"""JSON command interface. Remote and scheduling side effects require explicit flags."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, quote
from zoneinfo import ZoneInfoNotFoundError

from . import __version__
from .documents import (CalendarError, MAX_BYTES, UTC, parse, describe, parse_bound, iso,
                        create, patch, diff, invitation, instant)
from .sources import DAVClient, Transport, write_private
from .store import Store


def load(path):
    try:
        with (sys.stdin.buffer if path == "-" else open(path, "rb")) as f:
            raw = f.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise CalendarError("Input exceeds 32 MiB")
        return raw
    except OSError:
        raise CalendarError("Unable to read input file") from None


def save(path, raw):
    path = Path(path)
    # Never overwrite unrelated user files; callers must choose a new output path.
    try:
        with open(path, "xb") as f:
            os.chmod(path, 0o600)
            f.write(raw)
    except FileExistsError:
        raise CalendarError("Output already exists; choose a new path") from None
    return {"path": str(path), "bytes": len(raw)}


def parser():
    p = argparse.ArgumentParser(prog="calendar-cli", description="ICS/CalDAV calendar toolkit; JSON output")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--data", help="Private data directory (or CALENDAR_DATA)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    a = sub.add_parser("add", help="Register a source; sensitive configuration is read from JSON file/stdin")
    a.add_argument("name")
    a.add_argument("--kind", choices=["feed", "file", "caldav"], required=True)
    a.add_argument("--config", required=True, help='JSON file or -; keys: url, username/password, bearer, allow_http')
    a.add_argument("--timezone", default="UTC")
    a.add_argument("--past-years", type=int, default=5)
    a.add_argument("--future-years", type=int, default=1)
    a = sub.add_parser("configure", help="Rotate source credentials or change its expansion settings")
    a.add_argument("source")
    a.add_argument("--config", help="Replacement complete private source JSON")
    a.add_argument("--timezone")
    a.add_argument("--past-years", type=int)
    a.add_argument("--future-years", type=int)
    sub.add_parser("sources")
    sub.add_parser("calendars")
    sub.add_parser("status")
    a = sub.add_parser("sync")
    a.add_argument("source", nargs="?")
    a.add_argument("--all", action="store_true")
    a.add_argument("--since")
    a.add_argument("--until")
    a.add_argument("--cached", action="store_true", help="Expand stored source without fetching")
    a = sub.add_parser("coverage")
    a.add_argument("calendar")
    a.add_argument("--since")
    a.add_argument("--until")
    for command in ("search", "list"):
        a = sub.add_parser(command)
        if command == "search":
            a.add_argument("query", help="SQLite FTS5 expression")
        a.add_argument("--calendar")
        a.add_argument("--kind", choices=["VEVENT", "VTODO", "VJOURNAL", "VFREEBUSY"])
        a.add_argument("--limit", type=int, default=100)
        a.add_argument("--offset", type=int, default=0)
        a.add_argument("--history", action="store_true")
        a.add_argument("--no-sync", action="store_true")
    a = sub.add_parser("read")
    a.add_argument("id", type=int)
    for command in ("agenda", "conflicts", "freebusy"):
        a = sub.add_parser(command)
        a.add_argument("--since", required=True)
        a.add_argument("--until", required=True)
        a.add_argument("--calendar")
        a.add_argument("--no-sync", action="store_true")
    a = sub.add_parser("export")
    a.add_argument("calendar")
    a.add_argument("--revision")
    a.add_argument("--output", required=True)
    a = sub.add_parser("export-source", help="Export exact latest source bytes (feed/file)")
    a.add_argument("source")
    a.add_argument("--output", required=True)
    a = sub.add_parser("validate")
    a.add_argument("file")
    a = sub.add_parser("inspect")
    a.add_argument("file")
    a = sub.add_parser("diff")
    a.add_argument("before")
    a.add_argument("after")
    a = sub.add_parser("import", help="Import a private local draft; no remote write")
    a.add_argument("file")
    a.add_argument("--calendar", help="Record the source calendar/revision this draft is based on")
    a = sub.add_parser("create", help="Create a local draft from JSON property content lines")
    a.add_argument("--kind", choices=["VEVENT", "VTODO", "VJOURNAL", "VFREEBUSY"], default="VEVENT")
    a.add_argument("--uid")
    a.add_argument("--properties", required=True, help='JSON file, e.g. {"SUMMARY":"SUMMARY:Meeting"}')
    a = sub.add_parser("edit")
    a.add_argument("draft")
    a.add_argument("uid")
    a.add_argument("--recurrence-id", default="")
    a.add_argument("--properties", help="JSON file mapping property names to complete ICS content lines")
    a.add_argument("--remove", action="append", default=[])
    a.add_argument("--delete", action="store_true")
    a = sub.add_parser("draft-export")
    a.add_argument("draft")
    a.add_argument("--output", required=True)
    sub.add_parser("drafts")
    a = sub.add_parser("invitation")
    a.add_argument("file")
    a.add_argument("--method", choices=["REQUEST", "REPLY", "CANCEL", "PUBLISH"], required=True)
    a.add_argument("--attendee")
    a.add_argument("--partstat")
    a.add_argument("--output", required=True)
    a = sub.add_parser("attachment")
    a.add_argument("file")
    a.add_argument("uid")
    a.add_argument("--index", type=int, default=0)
    a.add_argument("--fetch", action="store_true", help="Explicitly fetch external HTTPS attachment")
    a.add_argument("--output", required=True)
    a = sub.add_parser("discover")
    a.add_argument("source")
    a = sub.add_parser("remote-query")
    a.add_argument("calendar")
    a.add_argument("--since", required=True)
    a.add_argument("--until", required=True)
    a.add_argument("--kind", choices=["VEVENT", "VTODO", "VJOURNAL", "VFREEBUSY"], default="VEVENT")
    a = sub.add_parser("resources")
    a.add_argument("calendar")
    for command in ("remote-put", "remote-delete"):
        a = sub.add_parser(command)
        a.add_argument("calendar")
        a.add_argument("resource", help="Existing resource basename, or new .ics basename")
        if command == "remote-put":
            a.add_argument("file")
            a.add_argument("--create", action="store_true")
        a.add_argument("--etag", help="Required for update/delete; use the version used when editing")
        a.add_argument("--confirm-remote", action="store_true")
        a.add_argument("--allow-scheduling", action="store_true", help="Allow server scheduling side effects on attendee-bearing resources")
    a = sub.add_parser("remote-create-calendar")
    a.add_argument("source")
    a.add_argument("--home", required=True, help="Advertised calendar home URL")
    a.add_argument("--name", required=True)
    a.add_argument("--confirm-remote", action="store_true")
    a = sub.add_parser("schedule")
    a.add_argument("calendar")
    a.add_argument("file")
    a.add_argument("--sender", required=True)
    a.add_argument("--recipient", action="append", required=True)
    a.add_argument("--confirm-send", action="store_true")
    return p


def refresh(store, calendar_id=None, start=None, end=None):
    if calendar_id:
        sources = [store.source(store.calendar(calendar_id)["source_id"])]
    else:
        sources = store.rows("SELECT * FROM sources")
    warnings = []
    for source in sources:
        fetched = parse_bound(source["fetched_at"]) if source["fetched_at"] else None
        fetch = fetched is None or datetime.now(UTC) - fetched > timedelta(seconds=60)
        try:
            if fetch or start is not None:
                store.sync(source["id"], start, end, fetch=fetch)
        except (CalendarError, OSError):
            warnings.append({"source_id": source["id"], "warning": "Sync unavailable; serving cached data"})
    return warnings


def busy_intervals(store, agenda, start, end, cid):
    intervals = []
    for o in agenda["occurrences"]:
        if o["kind"] == "VEVENT" and o["status"] != "CANCELLED" and not o["transparent"]:
            a, b = max(start, parse_bound(o["start"])), min(end, parse_bound(o["end"]))
            if a < b:
                intervals.append((a, b))
    # Explicit VFREEBUSY periods are not recurrence-expanded.
    freebusy_components = store.search(calendar_id=cid, kind="VFREEBUSY", limit=10000)
    if len(freebusy_components) == 10000:
        raise CalendarError("Too many free/busy components; select a narrower calendar")
    for row in freebusy_components:
        from icalendar import Component
        c = Component.from_ical(store.read(row["id"])["raw"])
        periods = c.get("FREEBUSY", [])
        for p in periods if isinstance(periods, list) else [periods]:
            if p.params.get("FBTYPE", "BUSY") == "FREE":
                continue
            a, b = p.dt
            if isinstance(b, timedelta):
                b = a + b
            a, b = max(start, instant(a, "UTC")), min(end, instant(b, "UTC"))
            if a < b:
                intervals.append((a, b))
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    free, cursor = [], start
    for a, b in merged:
        if cursor < a:
            free.append((cursor, a))
        cursor = b
    if cursor < end:
        free.append((cursor, end))
    return {"busy": [{"start": iso(a), "end": iso(b)} for a, b in merged],
            "free": [{"start": iso(a), "end": iso(b)} for a, b in free],
            "complete": agenda["complete"], "coverage": agenda["coverage"],
            "note": "Free intervals are provisional if coverage is incomplete or source is stale"}


def dav_for(store, cid):
    cal = store.calendar(cid)
    source = store.source(cal["source_id"])
    if source["kind"] != "caldav":
        raise CalendarError("ICS subscriptions are read-only remotely; use a CalDAV calendar")
    return cal, DAVClient(store.secrets.get(source["id"]))


def scheduling_guard(raw, allowed):
    if not allowed and any(c.get("ORGANIZER") or c.get("ATTENDEE") for cal in parse(raw) for c in cal.subcomponents):
        raise CalendarError("This resource may trigger server scheduling; explicit --allow-scheduling is required")


def execute(a):
    command = a.command
    if command in ("validate", "inspect"):
        calendars = parse(load(a.file))
        return {"valid": True, "calendars": [describe(c) for c in calendars]} if command == "inspect" else {
            "valid": True, "calendars": len(calendars),
            "note": "Structural/parser validation; not a complete RFC conformance certificate"}
    if command == "diff":
        return {"diff": diff(load(a.before), load(a.after))}
    if command == "invitation":
        return save(a.output, invitation(load(a.file), a.method, a.attendee, a.partstat))
    if command == "attachment":
        matches = [c for cal in parse(load(a.file)) for c in cal.subcomponents if str(c.get("UID", "")) == a.uid]
        if len(matches) != 1:
            raise CalendarError("Attachment component UID absent or ambiguous")
        values = matches[0].get("ATTACH", [])
        values = values if isinstance(values, list) else [values]
        if not 0 <= a.index < len(values):
            raise CalendarError("Attachment index out of range")
        value = values[a.index]
        if value.params.get("VALUE") == "BINARY" or value.params.get("ENCODING") == "BASE64":
            try:
                raw = base64.b64decode(value.to_ical(), validate=True)
            except ValueError:
                raise CalendarError("Invalid base64 attachment") from None
        elif a.fetch:
            _, _, raw, _ = Transport({"url": str(value)}).request("GET")
        else:
            raise CalendarError("External attachment requires --fetch; no request sent")
        return save(a.output, raw)
    store = Store(a.data)
    try:
        return execute_store(store, a)
    finally:
        store.db.close()


def execute_store(store, a):
    command = a.command
    if command == "doctor":
        store.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_probe USING fts5(text)")
        check = store.db.execute("PRAGMA quick_check").fetchone()[0]
        import shutil
        return {"ok": check == "ok", "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                "fts5": True, "database": check, "free_bytes": shutil.disk_usage(store.directory).free,
                "sources": len(store.status()["sources"]), "network_checked": False,
                "note": "Use sync to verify source credentials/network; doctor never sends calendar writes"}
    if command == "add":
        config = json.loads(load(a.config))
        if not isinstance(config, dict) or not isinstance(config.get("url"), str):
            raise CalendarError("Source config requires a string url")
        return store.add_source(a.name, a.kind, config, a.timezone, a.past_years, a.future_years)
    if command == "configure":
        source = store.source(a.source)
        from zoneinfo import ZoneInfo
        tz = a.timezone or source["timezone"]
        ZoneInfo(tz)
        past = source["past_years"] if a.past_years is None else a.past_years
        future = source["future_years"] if a.future_years is None else a.future_years
        if min(past, future) < 0:
            raise CalendarError("Backfill years must be nonnegative")
        if a.config:
            config = json.loads(load(a.config))
            if source["kind"] == "file":
                config["url"] = str(Path(config["url"]).expanduser().resolve())
            else:
                Transport(config)
            store.secrets.set(source["id"], config)
        with store.db:
            store.db.execute("UPDATE sources SET timezone=?,past_years=?,future_years=?,fetched_at=NULL,etag=NULL,modified=NULL WHERE id=?",
                             (tz, past, future, source["id"]))
            if a.config:
                store.db.execute("UPDATE calendars SET sync_token=NULL WHERE source_id=?", (source["id"],))
        return {"source_id": source["id"], "configured": True, "refresh_required": True}
    if command == "sources":
        return {"sources": store.status()["sources"]}
    if command == "calendars":
        return {"calendars": store.status()["calendars"]}
    if command == "status":
        return store.status()
    if command == "sync":
        if bool(a.source) == a.all:
            raise CalendarError("Choose one source or --all")
        start = parse_bound(a.since) if a.since else None
        end = parse_bound(a.until) if a.until else None
        sources = store.rows("SELECT id FROM sources") if a.all else [{"id": a.source}]
        return {"results": [store.sync(s["id"], start, end, fetch=not a.cached) for s in sources]}
    if command == "coverage":
        if bool(a.since) != bool(a.until):
            raise CalendarError("Supply both --since and --until")
        return store.coverage(a.calendar, parse_bound(a.since) if a.since else None, parse_bound(a.until) if a.until else None)
    if command in ("search", "list"):
        warnings = [] if a.no_sync else refresh(store, a.calendar)
        return {"results": store.search(getattr(a, "query", None), a.calendar, a.kind, a.limit, a.offset, a.history),
                "warnings": warnings, "sources": store.status()["sources"]}
    if command == "read":
        return store.read(a.id)
    if command in ("agenda", "conflicts", "freebusy"):
        start, end = parse_bound(a.since), parse_bound(a.until)
        warnings = [] if a.no_sync else refresh(store, a.calendar, start, end)
        result = store.agenda(start, end, a.calendar)
        if command == "conflicts":
            active = [o for o in result["occurrences"] if o["kind"] == "VEVENT" and
                      o["status"] != "CANCELLED" and not o["transparent"]]
            conflicts = []
            for i, left in enumerate(active):
                for j in range(i + 1, len(active)):
                    right = active[j]
                    if right["start"] >= left["end"]:
                        break
                    if right["end"] > left["start"] and left["end"] > left["start"] and right["end"] > right["start"]:
                        conflicts.append({"left": left, "right": right})
                        if len(conflicts) > 10000:
                            raise CalendarError("More than 10,000 conflicts; narrow the range/calendar")
            result = {"conflicts": conflicts, "coverage": result["coverage"], "complete": result["complete"]}
        elif command == "freebusy":
            result = busy_intervals(store, result, start, end, a.calendar)
        result["warnings"] = warnings
        result["sources"] = store.status()["sources"]
        return result
    if command == "export":
        return save(a.output, store.raw(a.calendar, a.revision))
    if command == "export-source":
        source = store.source(a.source)
        row = store.db.execute("SELECT raw FROM feeds WHERE source_id=?", (source["id"],)).fetchone()
        if not row:
            raise CalendarError("No cached feed/file source bytes")
        return save(a.output, row[0])
    if command == "import":
        return store.draft(load(a.file), a.calendar)
    if command == "create":
        return store.draft(create(a.kind, a.uid or str(uuid.uuid4()), json.loads(load(a.properties))))
    if command == "edit":
        raw = patch(store.draft_raw(a.draft), a.uid, a.recurrence_id,
                    json.loads(load(a.properties)) if a.properties else {}, a.remove, a.delete)
        return store.draft(raw, ident=a.draft)
    if command == "draft-export":
        return save(a.output, store.draft_raw(a.draft))
    if command == "drafts":
        return {"drafts": store.rows("SELECT id,calendar_id,base_revision,created_at FROM drafts")}
    if command == "discover":
        source = store.source(a.source)
        if source["kind"] != "caldav":
            raise CalendarError("Discovery requires a CalDAV source")
        # Explicit discovery returns hrefs needed for operations; normal status never does.
        dav = DAVClient(store.secrets.get(source["id"]))
        calendars = dav.discover()
        return {"calendars": calendars, "homes": dav.homes}
    if command == "remote-create-calendar":
        if not a.confirm_remote:
            raise CalendarError("Remote creation requires --confirm-remote")
        source = store.source(a.source)
        if source["kind"] != "caldav":
            raise CalendarError("Requires a CalDAV source")
        dav = DAVClient(store.secrets.get(source["id"]))
        dav.discover()
        if a.home not in dav.homes:
            raise CalendarError("Home must be an advertised home from discovery")
        return dav.make_calendar(urljoin(a.home.rstrip("/") + "/", uuid.uuid4().hex + "/"), a.name)
    if command == "resources":
        dav_for(store, a.calendar)
        return {"resources": [{"resource": r["href"].rsplit("/", 1)[-1], "etag": r["etag"]}
            for r in store.rows("SELECT href,etag FROM resources WHERE calendar_id=?", (a.calendar,))]}
    if command == "remote-query":
        cal, dav = dav_for(store, a.calendar)
        start, end = parse_bound(a.since), parse_bound(a.until)
        if start >= end:
            raise CalendarError("Window start must precede end")
        rows = dav.query(cal["source_key"], start, end, a.kind)
        from .sources import CAL, DAV
        return {"resources": [{"resource": r["href"].rsplit("/", 1)[-1],
            "etag": r["properties"]["{" + DAV + "}getetag"].text,
            "raw": r["properties"].get("{" + CAL + "}calendar-data").text
            if r["properties"].get("{" + CAL + "}calendar-data") is not None else None} for r in rows]}
    if command in ("remote-put", "remote-delete"):
        if not a.confirm_remote:
            raise CalendarError("Remote mutation requires --confirm-remote")
        cal, dav = dav_for(store, a.calendar)
        if not a.resource.endswith(".ics") or any(s in a.resource for s in ("/", "\\", "?", "#", "%")) or a.resource.startswith("."):
            raise CalendarError("Resource must be a plain .ics basename")
        href = urljoin(cal["source_key"].rstrip("/") + "/", quote(a.resource))
        existing = store.rows("SELECT * FROM resources WHERE calendar_id=? AND href=?", (a.calendar, href))
        if command == "remote-put" and a.create:
            if a.etag:
                raise CalendarError("Create cannot specify an ETag")
        elif not a.etag:
            raise CalendarError("Update/delete requires the ETag used when editing; obtain it from resources")
        elif not existing or existing[0]["etag"] != a.etag:
            raise CalendarError("Sync and review the cached resource matching this ETag before updating/deleting")
        if command == "remote-put":
            raw = load(a.file)
            supported = json.loads(cal["metadata"]).get("components", [])
            from .documents import KINDS
            if supported and any(c.name in KINDS and c.name not in supported for calendar in parse(raw) for c in calendar.subcomponents):
                raise CalendarError("Calendar does not advertise support for this component type")
            scheduling_guard(raw, a.allow_scheduling)
            if existing:
                scheduling_guard(existing[0]["raw"], a.allow_scheduling)
            result = dav.put(href, raw, None if a.create else a.etag)
        else:
            if not existing:
                raise CalendarError("Sync the resource before deleting it")
            scheduling_guard(existing[0]["raw"], a.allow_scheduling)
            result = dav.delete(href, a.etag)
        with store.db:
            store.db.execute("UPDATE sources SET fetched_at=NULL WHERE id=?", (cal["source_id"],))
        return result
    if command == "schedule":
        if not a.confirm_send:
            raise CalendarError("Sending invitations/replies requires --confirm-send")
        cal, dav = dav_for(store, a.calendar)
        return dav.schedule(json.loads(cal["metadata"]).get("outbox"), load(a.file), a.sender, a.recipient)
    raise CalendarError("Unknown command")


def main():
    try:
        args = parser().parse_args()
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (CalendarError, ZoneInfoNotFoundError, sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
        message = str(exc) if isinstance(exc, CalendarError) else "Invalid input or unavailable local dependency; run doctor"
        print(json.dumps({"error": message}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"error": "Interrupted; completed sync chunks are retained"}), file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({"error": "Unexpected failure; run doctor and report the error type", "type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
