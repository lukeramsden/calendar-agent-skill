from datetime import datetime
import pytest
from calendar_cli.documents import (parse, patch, create, expand, _expand, parse_bound, describe,
                                     CalendarError, container_keys, invitation)

A, B = parse_bound('2026-03-01'), parse_bound('2026-04-01')

def event(body):
    return ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nBEGIN:VEVENT\r\nUID:x\r\n'
            + body + '\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n').encode()

def test_preserve_edit(basic):
    after = patch(basic, 'meeting', '', {'SUMMARY': 'SUMMARY:New summary'})
    c = parse(after)[0]
    assert c['X-UNKNOWN'].params['X-PARAM'] == 'retained'
    e = c.walk('VEVENT')[0]
    assert e['X-CUSTOM'].params['X-ARG'] == 'yes'
    assert e.subcomponents[0].name == 'VALARM'
    assert e['SEQUENCE'] == 1
    assert str(e['SUMMARY']) == 'New summary'
    assert len(c.walk('VEVENT')) == 2

def test_recurrence(basic):
    result = expand(basic, A, B)
    events = [r for r in result if r['kind'] == 'VEVENT']
    assert len(events) == 3
    assert any(r['start'] == '2026-03-03T12:00:00+00:00' for r in events)
    assert not any(r['start'].startswith('2026-03-02') for r in events)

def test_dst_floating():
    raw = event('DTSTART:20260328T090000\r\nDTEND:20260328T100000\r\nRRULE:FREQ=DAILY;COUNT=3')
    rows = _expand(raw, A, B, 'Europe/London', 100)
    assert [r['start'][11:16] for r in rows] == ['09:00', '08:00', '08:00']

def test_all_day_overlap_and_exclusive_end():
    raw = event('DTSTART;VALUE=DATE:20260301\r\nDTEND;VALUE=DATE:20260304')
    assert len(_expand(raw, parse_bound('2026-03-03'), parse_bound('2026-03-04'), 'UTC', 100)) == 1
    assert not _expand(raw, parse_bound('2026-03-04'), parse_bound('2026-03-05'), 'UTC', 100)

def test_rdate_cancel():
    raw = event('DTSTART:20260301T090000Z\r\nDURATION:PT1H\r\nRDATE:20260303T090000Z\r\nSTATUS:CANCELLED')
    result = _expand(raw, A, B, 'UTC', 100)
    assert all(r['status'] == 'CANCELLED' for r in result)
    assert len(result) == 2

def test_limits():
    raw = event('DTSTART:20260301T090000Z\r\nRRULE:FREQ=DAILY;COUNT=5')
    with pytest.raises(CalendarError):
        _expand(raw, A, B, 'UTC', 2)
    with pytest.raises(CalendarError, match='timed out'):
        expand(raw, A, B, timeout=0.00001)

def test_malformed_and_duplicate(basic):
    with pytest.raises(CalendarError):
        parse(b'<html>error</html>')
    with pytest.raises(CalendarError):
        parse(basic.replace(b'UID:task', b'X-NO-UID:task'))
    c = parse(basic)[0]
    c.add_component(c.walk('VEVENT')[0])
    with pytest.raises(CalendarError, match='Duplicate'):
        parse(c.to_ical())

def test_containers():
    raw = b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n'
    with pytest.raises(CalendarError, match='anonymous'):
        container_keys(parse(raw + raw))
    a = raw.replace(b'VERSION:2.0', b'VERSION:2.0\r\nX-WR-CALNAME:One')
    b = raw.replace(b'VERSION:2.0', b'VERSION:2.0\r\nX-WR-CALNAME:Two')
    assert container_keys(parse(a + b)) == list(reversed(container_keys(parse(b + a))))

def test_creation_patch_injection():
    raw = create('VEVENT', 'u', {'DTSTART': 'DTSTART;VALUE=DATE:20260301', 'SUMMARY': 'SUMMARY:Hello'})
    assert parse(raw)[0].walk('VEVENT')[0]['DTSTART'].dt.isoformat() == '2026-03-01'
    with pytest.raises(CalendarError):
        patch(raw, 'u', '', {'SUMMARY': 'SUMMARY:bad\nATTENDEE:mailto:x@y'})
    with pytest.raises(CalendarError):
        patch(raw, 'u', '', {'SUMMARY': 'LOCATION:bad'})

def test_invitation_reply():
    raw = event('DTSTART:20260301T090000Z\r\nORGANIZER:mailto:host@example.test\r\nATTENDEE:mailto:guest@example.test')
    reply = parse(invitation(raw, 'REPLY', 'mailto:guest@example.test', 'ACCEPTED'))[0]
    assert reply['METHOD'] == 'REPLY'
    assert reply.walk('VEVENT')[0]['ATTENDEE'].params['PARTSTAT'] == 'ACCEPTED'
