"""Private source configuration and bounded HTTP/CalDAV operations."""
from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

import requests
from defusedxml import ElementTree as SafeET

from .documents import CalendarError, MAX_BYTES, parse

DAV = "DAV:"
CAL = "urn:ietf:params:xml:ns:caldav"
NS = {"d": DAV, "c": CAL}


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise CalendarError("Private data directory must not be a symbolic link")
    path.chmod(0o700)
    return path


def write_private(path, data):
    path = Path(path)
    private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp-" + os.urandom(8).hex())
    try:
        with open(tmp, "xb") as f:
            os.chmod(tmp, 0o600)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class Secrets:
    def __init__(self, directory):
        self.directory = private_dir(directory)

    def set(self, source_id, values):
        payload = json.dumps(values)
        if platform.system() == "Darwin" and not os.environ.get("CALENDAR_FILE_SECRETS"):
            import keyring
            try:
                keyring.set_password("calendar-agent-skill", source_id, payload)
            except Exception:
                raise CalendarError("Keychain write failed; unlock Keychain or explicitly use file secrets") from None
        else:
            write_private(self.directory / (source_id + ".json"), payload.encode())

    def get(self, source_id):
        try:
            if platform.system() == "Darwin" and not os.environ.get("CALENDAR_FILE_SECRETS"):
                import keyring
                payload = keyring.get_password("calendar-agent-skill", source_id)
            else:
                payload = (self.directory / (source_id + ".json")).read_text()
            return json.loads(payload)
        except Exception:
            raise CalendarError("Source credentials unavailable; register credentials again") from None


def origin(url):
    p = urlsplit(url)
    return p.scheme.lower(), (p.hostname or "").lower(), p.port or (443 if p.scheme == "https" else 80)


class Transport:
    def __init__(self, config):
        self.url = config["url"].replace("webcal://", "https://", 1)
        self.allow_http = config.get("allow_http", False)
        self.session = requests.Session()
        self.session.trust_env = False  # do not leak private feeds via ambient proxies or .netrc
        if config.get("username") is not None:
            self.session.auth = (config["username"], config.get("password", ""))
        if config.get("bearer"):
            self.session.headers["Authorization"] = "Bearer " + config["bearer"]
        self.check_url(self.url)

    def check_url(self, url):
        try:
            p = urlsplit(url)
            if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password or p.fragment:
                raise ValueError()
            if p.scheme == "http" and not self.allow_http:
                raise CalendarError("HTTP requires explicit --allow-http; prefer verified HTTPS")
            if origin(url) != origin(self.url):
                raise CalendarError("Cross-origin DAV href or redirect refused; register destination explicitly")
        except CalendarError:
            raise
        except Exception:
            raise CalendarError("Invalid source URL") from None

    def request(self, method, url=None, headers=None, body=None, expected=(200,)):
        url = url or self.url
        self.check_url(url)
        for redirects in range(6):
            try:
                with self.session.request(method, url, headers=headers, data=body, timeout=(10, 45),
                                          allow_redirects=False, stream=True) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        if method not in ("GET", "HEAD", "OPTIONS", "PROPFIND"):
                            raise CalendarError("Redirect of a remote mutation/report refused")
                        target = urljoin(url, response.headers.get("Location", ""))
                        self.check_url(target)
                        if target == url:
                            raise CalendarError("Invalid redirect")
                        url = target
                        continue
                    if response.status_code not in expected:
                        if response.status_code in (409, 412) and method in ("PUT", "DELETE", "MKCALENDAR", "POST"):
                            raise CalendarError("Remote version conflict; refresh and review before retrying")
                        raise HTTPError(response.status_code)
                    content = bytearray()
                    for chunk in response.iter_content(65536):
                        content.extend(chunk)
                        if len(content) > MAX_BYTES:
                            raise CalendarError("Response exceeds 32 MiB limit")
                    return response.status_code, dict(response.headers), bytes(content), url
            except CalendarError:
                raise
            except requests.RequestException:
                raise CalendarError("Network/TLS request failed (private URL redacted)") from None
        raise CalendarError("Too many redirects")

    def feed(self, etag=None, modified=None):
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if modified:
            headers["If-Modified-Since"] = modified
        return self.request("GET", headers=headers, expected=(200, 304))


class HTTPError(CalendarError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Remote server returned HTTP {status} (URL redacted)")


def xml(raw):
    try:
        return SafeET.fromstring(raw)
    except Exception:
        raise CalendarError("Invalid or unsafe DAV XML response") from None


def element(tag, text=None, **attributes):
    prefix, name = tag.split(":", 1)
    el = ET.Element("{" + NS[prefix] + "}" + name, attributes)
    el.text = text
    return el


def properties_request(names):
    root = element("d:propfind")
    prop = element("d:prop")
    root.append(prop)
    for name in names:
        prop.append(element(name))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


class DAVClient:
    """DAV wire semantics live here, including conditional mutations and token fallback."""
    def __init__(self, config):
        self.http = Transport(config)
        self.homes = []

    def propfind(self, url, names, depth="0"):
        _, _, raw, resolved = self.http.request("PROPFIND", url,
            {"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
            properties_request(names), expected=(207,))
        return self.responses(raw, resolved)

    def responses(self, raw, base):
        root = xml(raw)
        if root.tag != "{" + DAV + "}multistatus":
            raise CalendarError("Expected DAV multistatus response")
        rows = []
        for response in root.findall("d:response", NS):
            href_text = response.findtext("d:href", namespaces=NS)
            if not href_text:
                raise CalendarError("DAV response is missing its resource href")
            href = urljoin(base, href_text)
            self.http.check_url(href)
            properties = {}
            failures = []
            for propstat in response.findall("d:propstat", NS):
                status = propstat.findtext("d:status", default="", namespaces=NS)
                if " 200 " in status:
                    prop = propstat.find("d:prop", NS)
                    if prop is not None:
                        properties.update({p.tag: p for p in prop})
                else:
                    failures.append(status)
            rows.append({"href": href, "properties": properties,
                         "status": response.findtext("d:status", default="", namespaces=NS),
                         "failures": failures})
        return rows, root.findtext("d:sync-token", namespaces=NS)

    def discover(self):
        base = self.http.url
        rows, _ = self.propfind(base, ["d:current-user-principal", "c:calendar-home-set", "d:resourcetype"])
        if not rows:
            raise CalendarError("DAV discovery returned no resources")
        p = rows[0]["properties"]
        principal_prop = p.get("{" + DAV + "}current-user-principal")
        principal = urljoin(base, principal_prop.findtext("d:href", default="", namespaces=NS)) if principal_prop is not None else base
        rows, _ = self.propfind(principal, ["c:calendar-home-set", "c:schedule-outbox-URL", "c:calendar-user-address-set"])
        if not rows:
            raise CalendarError("DAV principal unavailable")
        props = rows[0]["properties"]
        home = props.get("{" + CAL + "}calendar-home-set")
        if home is None:
            raise CalendarError("No CalDAV calendar-home-set; register a principal or well-known CalDAV URL")
        outbox = props.get("{" + CAL + "}schedule-outbox-URL")
        outbox_url = urljoin(principal, outbox.findtext("d:href", default="", namespaces=NS)) if outbox is not None else None
        calendars = []
        for href in home.findall("d:href", NS):
            home_url = urljoin(principal, href.text or "")
            self.http.check_url(home_url)
            self.homes.append(home_url)
            found, _ = self.propfind(home_url, ["d:resourcetype", "d:displayname", "d:sync-token",
                "c:supported-calendar-component-set", "d:supported-report-set", "c:calendar-description"], "1")
            for row in found:
                prop = row["properties"]
                kind = prop.get("{" + DAV + "}resourcetype")
                if kind is None or kind.find("c:calendar", NS) is None:
                    continue
                def text(name):
                    node = prop.get(name)
                    return node.text if node is not None else None
                supported = prop.get("{" + CAL + "}supported-calendar-component-set")
                calendars.append({"href": row["href"], "home": home_url,
                    "name": text("{" + DAV + "}displayname") or "Unnamed calendar",
                    "description": text("{" + CAL + "}calendar-description"),
                    "components": [c.get("name") for c in supported] if supported is not None else [],
                    "outbox": outbox_url})
        return calendars

    def listing(self, url, token=None):
        """Return changed resources, tombstones and next token; fallback is a complete listing."""
        if token:
            root = element("d:sync-collection")
            root.extend([element("d:sync-token", token), element("d:sync-level", "1")])
            prop = element("d:prop")
            prop.append(element("d:getetag"))
            root.append(prop)
            try:
                _, _, raw, resolved = self.http.request("REPORT", url,
                    {"Depth": "1", "Content-Type": "application/xml"}, ET.tostring(root), (207,))
                rows, next_token = self.responses(raw, resolved)
                if not next_token:
                    raise CalendarError("Sync response missing token")
                self.check_listing(rows)
                return rows, next_token, False
            except HTTPError as exc:
                if exc.status not in (400, 403, 405, 409, 501):
                    raise
        rows, _ = self.propfind(url, ["d:getetag", "d:resourcetype", "d:sync-token"], "1")
        next_token = None
        resources = []
        found_collection = False
        for row in rows:
            if row["href"].rstrip("/") == url.rstrip("/"):
                kind = row["properties"].get("{" + DAV + "}resourcetype")
                if kind is None or kind.find("d:collection", NS) is None:
                    raise CalendarError("DAV listing does not confirm the calendar collection")
                found_collection = True
                node = row["properties"].get("{" + DAV + "}sync-token")
                next_token = node.text if node is not None else None
            else:
                kind = row["properties"].get("{" + DAV + "}resourcetype")
                if kind is not None and kind.find("d:collection", NS) is not None:
                    continue
                resources.append(row)
        if not found_collection:
            raise CalendarError("Incomplete DAV listing; calendar collection is absent")
        self.check_listing(resources)
        return resources, next_token, True

    @staticmethod
    def check_listing(rows):
        for row in rows:
            if row["status"] and " 404 " not in row["status"] and " 200 " not in row["status"]:
                raise CalendarError("Incomplete DAV listing; no reconciliation performed")
            if " 404 " not in row["status"] and "{" + DAV + "}getetag" not in row["properties"]:
                raise CalendarError("DAV resource missing ETag; no reconciliation performed")

    def query(self, url, start, end, kind="VEVENT"):
        root = element("c:calendar-query")
        prop = element("d:prop")
        prop.extend([element("d:getetag"), element("c:calendar-data")])
        filt = element("c:filter")
        outer = element("c:comp-filter", name="VCALENDAR")
        inner = element("c:comp-filter", name=kind)
        inner.append(element("c:time-range", start=start.strftime("%Y%m%dT%H%M%SZ"), end=end.strftime("%Y%m%dT%H%M%SZ")))
        outer.append(inner)
        filt.append(outer)
        root.extend([prop, filt])
        _, _, raw, resolved = self.http.request("REPORT", url,
            {"Depth": "1", "Content-Type": "application/xml"}, ET.tostring(root), (207,))
        rows, _ = self.responses(raw, resolved)
        self.check_listing(rows)
        return rows

    def get(self, href):
        _, headers, raw, _ = self.http.request("GET", href)
        parse(raw)
        etag = headers.get("ETag")
        if not etag or etag.startswith("W/"):
            raise CalendarError("Resource has no strong ETag; cannot safely edit")
        return raw, etag

    def put(self, href, raw, etag=None):
        from .documents import KINDS
        calendars = parse(raw)
        identities = {(c.name, str(c.get("UID", ""))) for cal in calendars for c in cal.subcomponents if c.name in KINDS}
        if len(calendars) != 1 or len(identities) != 1 or calendars[0].get("METHOD"):
            raise CalendarError("CalDAV PUT requires one UID series/component type and no METHOD property")
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag:
            if etag.startswith("W/"):
                raise CalendarError("A strong ETag is required")
            headers["If-Match"] = etag
        else:
            headers["If-None-Match"] = "*"
        _, response_headers, _, _ = self.http.request("PUT", href, headers, raw, (200, 201, 204))
        return {"written": True, "etag": response_headers.get("ETag"), "refresh_required": True}

    def delete(self, href, etag):
        if not etag or etag.startswith("W/"):
            raise CalendarError("A strong ETag is required for deletion")
        self.http.request("DELETE", href, {"If-Match": etag}, expected=(200, 204))
        return {"deleted": True, "refresh_required": True}

    def make_calendar(self, href, name):
        root = element("c:mkcalendar")
        setter, prop = element("d:set"), element("d:prop")
        prop.append(element("d:displayname", name))
        setter.append(prop)
        root.append(setter)
        self.http.request("MKCALENDAR", href, {"Content-Type": "application/xml", "If-None-Match": "*"},
                          ET.tostring(root), (201,))
        return {"created": True}

    def schedule(self, outbox, raw, sender, recipients):
        cal = parse(raw)
        if len(cal) != 1 or str(cal[0].get("METHOD", "")) not in ("REQUEST", "REPLY", "CANCEL"):
            raise CalendarError("Scheduling requires a REQUEST/REPLY/CANCEL document")
        if not outbox:
            raise CalendarError("Server did not advertise scheduling outbox support")
        if not recipients or any("\n" in s or "\r" in s for s in [sender, *recipients]):
            raise CalendarError("Invalid scheduling addresses")
        _, _, response, _ = self.http.request("POST", outbox,
            {"Content-Type": "text/calendar; charset=utf-8", "Originator": sender,
             "Recipient": ", ".join(recipients)}, raw, (200,))
        root = xml(response)
        statuses = [{"recipient": r.findtext("c:recipient/d:href", namespaces=NS),
                     "status": r.findtext("c:request-status", namespaces=NS)}
                    for r in root.findall("c:response", NS)]
        if not statuses:
            raise CalendarError("Scheduling response has no delivery statuses; do not retry automatically")
        return {"responses": statuses}
