"""Calendar documents and bounded occurrence expansion, independent of storage."""
from __future__ import annotations

import copy
import difflib
import hashlib
import multiprocessing
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar, Component, Event, Todo, Journal, FreeBusy
import recurring_ical_events

UTC = timezone.utc
MAX_BYTES = 32 * 1024 * 1024
KINDS = {"VEVENT", "VTODO", "VJOURNAL", "VFREEBUSY"}


class CalendarError(Exception):
    """A safe, user-facing error; never include credentials or private payloads."""


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse(raw: bytes) -> list[Calendar]:
    if len(raw) > MAX_BYTES:
        raise CalendarError("Calendar exceeds the 32 MiB document limit")
    try:
        calendars = Calendar.from_ical(raw, multiple=True)
        if not calendars or any(c.name != "VCALENDAR" for c in calendars):
            raise ValueError()
        for calendar in calendars:
            if str(calendar.get("VERSION", "")) != "2.0":
                raise CalendarError("Only iCalendar VERSION:2.0 is supported")
            seen = set()
            for c in calendar.walk():
                if c.errors:
                    raise CalendarError("Calendar contains invalid properties")
                if c.name in KINDS:
                    if not c.get("UID"):
                        raise CalendarError("Calendar component is missing UID")
                    key = identity(c)
                    if key in seen:
                        raise CalendarError("Duplicate component UID/type/recurrence identity")
                    seen.add(key)
                    for name in ("DTSTART", "DTEND", "DUE", "RECURRENCE-ID"):
                        prop = c.get(name)
                        if prop is not None and prop.params.get("TZID"):
                            value = prop.dt
                            if isinstance(value, datetime) and value.tzinfo is None:
                                raise CalendarError("Unresolved TZID; supply its VTIMEZONE definition")
        return calendars
    except CalendarError:
        raise
    except Exception:
        raise CalendarError("Unable to parse iCalendar document") from None


def identity(component):
    return (component.name, str(component.get("UID", "")),
            component.get("RECURRENCE-ID").to_ical().decode() if component.get("RECURRENCE-ID") else "")


def container_keys(calendars):
    """Reject ambiguous anonymous multi-calendar streams instead of guessing order."""
    keys = []
    for c in calendars:
        if c.get("UID"):
            key = "uid:" + str(c["UID"])
        elif len(calendars) == 1:
            key = "default"
        elif c.get("X-WR-CALNAME") or c.get("NAME"):
            key = "name:" + str(c.get("X-WR-CALNAME", c.get("NAME")))
        else:
            raise CalendarError("Multiple anonymous calendars require distinct names or UIDs")
        if key in keys:
            raise CalendarError("Ambiguous calendar identities in source")
        keys.append(key)
    return keys


def json_value(value):
    if isinstance(value, list):
        return [json_value(v) for v in value]
    if hasattr(value, "dt"):
        d = value.dt
        return d.isoformat() if hasattr(d, "isoformat") else str(d)
    if hasattr(value, "to_ical"):
        return value.to_ical().decode("utf-8", "replace")
    return str(value)


def describe(c):
    return {"type": c.name, "properties": {
        name: [{"value": json_value(v), "params": {k: json_value(p) for k, p in v.params.items()}}
               for v in (value if isinstance(value, list) else [value])]
        for name, value in c.items()}, "children": [describe(child) for child in c.subcomponents]}


def instant(value, tz: str):
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time())
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(tz))
    return value.astimezone(UTC)


def parse_bound(value: str, tz="UTC"):
    try:
        return instant(datetime.fromisoformat(value.replace("Z", "+00:00")), tz)
    except (ValueError, KeyError):
        raise CalendarError("Invalid ISO date/time or timezone") from None


def iso(value):
    return value.astimezone(UTC).isoformat()


def _expand(raw, start, end, tz, limit):
    calendar = parse(raw)[0]
    # A configured timezone applies to floating values, including DST transitions.
    # Recurrence library respects X-WR-TIMEZONE; make the selected interpretation explicit.
    calendar["X-WR-TIMEZONE"] = tz
    results = []
    returned_bytes = 0
    query = recurring_ical_events.of(calendar, components=("VEVENT", "VTODO", "VJOURNAL"))
    for c in query.between(start.astimezone(ZoneInfo(tz)), end.astimezone(ZoneInfo(tz))):
        begin = c.get("DTSTART", c.get("DUE"))
        if begin is None:
            continue
        a = instant(begin.dt, tz)
        finish = c.get("DTEND", c.get("DUE"))
        duration = c.get("DURATION")
        if finish is not None:
            b = instant(finish.dt, tz)
        elif duration is not None:
            b = instant(begin.dt + duration.dt, tz)
        elif isinstance(begin.dt, date) and not isinstance(begin.dt, datetime) and c.name == "VEVENT":
            b = instant(begin.dt + timedelta(days=1), tz)
        else:
            b = a
        if not (a < end and (b > start or (b == a and a >= start))):
            continue
        if b < a:
            raise CalendarError("Component ends before it starts")
        encoded = c.to_ical().decode()
        returned_bytes += len(encoded.encode())
        if returned_bytes > MAX_BYTES:
            raise CalendarError("Expanded payload exceeds 32 MiB; request a smaller window")
        results.append({"uid": str(c["UID"]), "kind": c.name,
                        "recurrence_id": identity(c)[2], "start": iso(a), "end": iso(b),
                        "all_day": not isinstance(begin.dt, datetime),
                        "summary": str(c.get("SUMMARY", "")),
                        "status": str(c.get("STATUS", "")),
                        "transparent": str(c.get("TRANSP", "OPAQUE")) == "TRANSPARENT",
                        "raw": encoded})
        if len(results) > limit:
            raise CalendarError("Occurrence limit exceeded; request a smaller window")
    return results


def _expansion_worker(connection, args):
    try:
        import resource
        # Bound CPU on both supported platforms and virtual memory on Linux.
        # macOS does not reliably enforce RLIMIT_AS for Python's allocator.
        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
        import sys
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        connection.send((True, _expand(*args)))
    except Exception:
        connection.send((False, "Recurrence expansion failed; inspect/validate source"))
    finally:
        connection.close()


def expand(raw, start, end, tz="UTC", limit=50000, timeout=30):
    """Expand in a killable process: tiny SECONDLY rules can otherwise exhaust memory."""
    if start >= end:
        raise CalendarError("Window start must be before end")
    ZoneInfo(tz)
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_expansion_worker, args=(writer, (raw, start, end, tz, limit)))
    process.start()
    writer.close()
    try:
        if not reader.poll(timeout):
            raise CalendarError("Recurrence expansion timed out; request a smaller window")
        try:
            ok, result = reader.recv()
        except EOFError:
            raise CalendarError("Recurrence worker stopped before completing the window") from None
        if not ok:
            raise CalendarError(result)
        return result
    finally:
        reader.close()
        if process.is_alive():
            process.terminate()
        process.join()


def patch(raw: bytes, uid: str, recurrence_id: str, properties: dict, remove=(), delete=False):
    """Property values are ICS content lines, enabling typed values and arbitrary extensions."""
    calendars = parse(raw)
    if len(calendars) != 1:
        raise CalendarError("Edit one calendar at a time")
    cal = calendars[0]
    matches = [c for c in cal.subcomponents if identity(c)[1:] == (uid, recurrence_id)]
    if len(matches) != 1:
        raise CalendarError("Component identity is absent or ambiguous")
    target = matches[0]
    if delete:
        cal.subcomponents.remove(target)
    else:
        for name in remove:
            target.pop(name.upper(), None)
        for name, content_lines in properties.items():
            name = name.upper()
            if name in ("BEGIN", "END"):
                raise CalendarError("Cannot patch component boundaries")
            lines = content_lines if isinstance(content_lines, list) else [content_lines]
            target.pop(name, None)
            for line in lines:
                if not isinstance(line, str) or "\n" in line or "\r" in line:
                    raise CalendarError("Patch values must be single ICS content lines")
                fragment = Component.from_ical((f"BEGIN:{target.name}\r\n{line}\r\nEND:{target.name}\r\n").encode())
                if list(fragment.keys()) != [name] or fragment.errors:
                    raise CalendarError("Patch property name does not match content line")
                values = fragment[name]
                for value in values if isinstance(values, list) else [values]:
                    target.add(name, value, encode=False)
        target["SEQUENCE"] = int(target.get("SEQUENCE", 0)) + 1
        target.pop("DTSTAMP", None)
        target.add("DTSTAMP", datetime.now(UTC))
    encoded = cal.to_ical()
    parse(encoded)
    return encoded


def create(kind, uid, properties):
    cls = {"VEVENT": Event, "VTODO": Todo, "VJOURNAL": Journal, "VFREEBUSY": FreeBusy}.get(kind)
    if cls is None:
        raise CalendarError("Unsupported component type")
    cal = Calendar()
    cal.add("VERSION", "2.0")
    cal.add("PRODID", "-//calendar-agent-skill//EN")
    component = cls()
    component.add("UID", uid)
    cal.add_component(component)
    return patch(cal.to_ical(), uid, "", properties)


def diff(before, after):
    return "".join(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True),
                                         fromfile="before.ics", tofile="after.ics"))


def invitation(raw, method, attendee=None, partstat=None):
    calendars = parse(raw)
    if len(calendars) != 1 or method not in ("REQUEST", "REPLY", "CANCEL", "PUBLISH"):
        raise CalendarError("Choose one calendar and REQUEST/REPLY/CANCEL/PUBLISH")
    cal = copy.deepcopy(calendars[0])
    cal["METHOD"] = method
    for c in cal.subcomponents:
        if c.name not in KINDS:
            continue
        if method != "PUBLISH" and not c.get("ORGANIZER"):
            raise CalendarError("Scheduling requires ORGANIZER")
        if method == "CANCEL":
            c["STATUS"] = "CANCELLED"
            c["SEQUENCE"] = int(c.get("SEQUENCE", 0)) + 1
        if method == "REPLY":
            values = c.get("ATTENDEE", [])
            values = values if isinstance(values, list) else [values]
            matches = [v for v in values if str(v).lower() == (attendee or "").lower()]
            if len(matches) != 1 or partstat not in ("ACCEPTED", "DECLINED", "TENTATIVE", "DELEGATED"):
                raise CalendarError("Reply requires an existing attendee and valid participation status")
            c.pop("ATTENDEE", None)
            matches[0].params["PARTSTAT"] = partstat
            c.add("ATTENDEE", matches[0], encode=False)
        c.pop("DTSTAMP", None)
        c.add("DTSTAMP", datetime.now(UTC))
    return cal.to_ical()
