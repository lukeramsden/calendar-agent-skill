import json
import time
from datetime import timedelta

import pytest
from calendar_cli.documents import parse, _expand, parse_bound, CalendarError, create
from calendar_cli.sources import DAVClient, HTTPError
from calendar_cli.cli import busy_intervals, execute, parser
from test_documents import event

A, B = parse_bound('2026-03-01'), parse_bound('2026-04-01')

def test_thisandfuture():
    raw = event('DTSTART:20260301T090000Z\r\nDTEND:20260301T100000Z\r\nRRULE:FREQ=DAILY;COUNT=4')
    override = b'''BEGIN:VEVENT\r
UID:x\r
RECURRENCE-ID;RANGE=THISANDFUTURE:20260302T090000Z\r
DTSTART:20260302T110000Z\r
DTEND:20260302T120000Z\r
END:VEVENT\r
'''
    raw = raw.replace(b'END:VCALENDAR', override + b'END:VCALENDAR')
    rows = _expand(raw, A, B, 'UTC', 100)
    assert [r['start'][11:16] for r in rows] == ['09:00', '11:00', '11:00', '11:00']

def test_cancelled_override():
    raw = event('DTSTART:20260301T090000Z\r\nRRULE:FREQ=DAILY;COUNT=2')
    override = b'''BEGIN:VEVENT\r
UID:x\r
RECURRENCE-ID:20260302T090000Z\r
DTSTART:20260302T090000Z\r
STATUS:CANCELLED\r
END:VEVENT\r
'''
    rows = _expand(raw.replace(b'END:VCALENDAR', override + b'END:VCALENDAR'), A, B, 'UTC', 100)
    assert len(rows) == 2
    assert rows[1]['status'] == 'CANCELLED'

def test_custom_timezone():
    raw = event('DTSTART;TZID=Custom-Test:20260301T090000\r\nDTEND;TZID=Custom-Test:20260301T100000')
    zone = b'''BEGIN:VTIMEZONE\r
TZID:Custom-Test\r
BEGIN:STANDARD\r
DTSTART:19700101T000000\r
TZOFFSETFROM:+0200\r
TZOFFSETTO:+0200\r
TZNAME:TEST\r
END:STANDARD\r
END:VTIMEZONE\r
'''
    raw = raw.replace(b'BEGIN:VEVENT', zone + b'BEGIN:VEVENT')
    assert _expand(raw, A, B, 'UTC', 100)[0]['start'] == '2026-03-01T07:00:00+00:00'
    with pytest.raises(CalendarError):
        parse(event('DTSTART;TZID=Unresolvable-Random-Zone:20260301T090000'))

def test_scheduling_statuses_and_unsupported(monkeypatch):
    dav = DAVClient({'url': 'https://example.test/'})
    raw = create('VEVENT', 'u', {'DTSTART': 'DTSTART:20260301T090000Z', 'ORGANIZER': 'ORGANIZER:mailto:a@example.test'})
    from calendar_cli.documents import invitation
    raw = invitation(raw, 'REQUEST')
    body = b'''<c:schedule-response xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:d="DAV:">
<c:response><c:recipient><d:href>mailto:b@example.test</d:href></c:recipient><c:request-status>2.0;Success</c:request-status></c:response>
<c:response><c:recipient><d:href>mailto:c@example.test</d:href></c:recipient><c:request-status>3.7;Invalid Calendar User</c:request-status></c:response>
</c:schedule-response>'''
    calls = []
    def request(*args, **kw):
        calls.append(args)
        return 200, {}, body, 'https://example.test/outbox'
    monkeypatch.setattr(dav.http, 'request', request)
    result = dav.schedule('https://example.test/outbox', raw, 'mailto:a@example.test', ['mailto:b@example.test', 'mailto:c@example.test'])
    assert len(result['responses']) == 2
    assert result['responses'][1]['status'].startswith('3.7')
    assert calls[0][0] == 'POST'
    with pytest.raises(CalendarError, match='outbox'):
        dav.schedule(None, raw, 'mailto:a@example.test', ['mailto:b@example.test'])

def test_unsupported_sync_falls_back(monkeypatch):
    dav = DAVClient({'url': 'https://example.test/'})
    def unsupported(*args, **kw):
        raise HTTPError(405)
    monkeypatch.setattr(dav.http, 'request', unsupported)
    from calendar_cli.sources import element
    kind = element('d:resourcetype')
    kind.append(element('d:collection'))
    monkeypatch.setattr(dav, 'propfind', lambda *args: ([{'href': 'https://example.test/calendar/',
        'properties': {'{DAV:}resourcetype': kind}}], None))
    rows, token, full = dav.listing('https://example.test/calendar/', 'old-token')
    assert rows == [] and token is None and full

def test_search_performance(store, tmp_path):
    from icalendar import Calendar, Event
    cal = Calendar()
    cal.add('VERSION', '2.0')
    cal.add('PRODID', '-//Synthetic benchmark//EN')
    for i in range(10000):
        e = Event()
        e.add('UID', str(i))
        e.add('SUMMARY', f'Archive appointment {i}')
        e.add('DESCRIPTION', 'release planning' if i % 10 == 0 else 'general appointment')
        e.add('DTSTART', A + timedelta(minutes=i))
        cal.add_component(e)
    path = tmp_path / 'benchmark.ics'
    path.write_bytes(cal.to_ical())
    source = store.add_source('benchmark', 'file', {'url': str(path)})
    store._fetch_feed(store.source(source['id']), {'url': str(path)})
    start = time.perf_counter()
    result = store.search('release', limit=10000)
    elapsed = time.perf_counter() - start
    assert len(result) == 1000
    # Generous guard against broken indexing, not a machine-specific latency claim.
    assert elapsed < 5
    print(f'FTS benchmark: 10,000 components, 1,000 matches, {elapsed:.4f}s')
