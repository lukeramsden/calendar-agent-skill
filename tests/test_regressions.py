import json
import fcntl
from pathlib import Path

import pytest
from icalendar import Calendar
from calendar_cli.documents import create, parse, patch, parse_bound, CalendarError
from calendar_cli.cli import execute, parser
from calendar_cli.sources import DAVClient

A, B = parse_bound('2026-03-01'), parse_bound('2026-03-02')

def test_conflicts_transparency_and_cancellation(store, tmp_path):
    cal = Calendar()
    cal.add('VERSION', '2.0')
    for uid, start, end, extra in [
        ('a', '090000', '110000', {}), ('b', '100000', '120000', {}),
        ('transparent', '090000', '130000', {'TRANSP': 'TRANSP:TRANSPARENT'}),
        ('cancelled', '090000', '130000', {'STATUS': 'STATUS:CANCELLED'}),
    ]:
        raw = create('VEVENT', uid, {'DTSTART': f'DTSTART:20260301T{start}Z',
            'DTEND': f'DTEND:20260301T{end}Z', **extra})
        cal.add_component(parse(raw)[0].walk('VEVENT')[0])
    path = tmp_path / 'conflicts.ics'
    path.write_bytes(cal.to_ical())
    source = store.add_source('overlap', 'file', {'url': str(path)})
    store.sync(source['id'], A, B)
    common = ['--data', str(store.directory)]
    window = ['--since', '2026-03-01', '--until', '2026-03-02', '--no-sync']
    conflicts = execute(parser().parse_args(common + ['conflicts'] + window))
    assert len(conflicts['conflicts']) == 1
    assert conflicts['complete']
    busy = execute(parser().parse_args(common + ['freebusy'] + window))
    assert busy['busy'] == [{'start': '2026-03-01T09:00:00+00:00', 'end': '2026-03-01T12:00:00+00:00'}]

def test_local_delete_history_and_create(store):
    raw = create('VTODO', 'task', {'SUMMARY': 'SUMMARY:Local task'})
    draft = store.draft(raw)['draft_id']
    changed = patch(raw, 'task', '', {}, delete=True)
    store.draft(changed, ident=draft)
    assert not parse(store.draft_raw(draft))[0].walk('VTODO')
    assert store.db.execute('SELECT raw FROM mutation_history WHERE draft_id=?', (draft,)).fetchone()[0] == raw

def test_incomplete_listing_refused(monkeypatch):
    dav = DAVClient({'url': 'https://example.test/'})
    monkeypatch.setattr(dav, 'propfind', lambda *args: ([], None))
    with pytest.raises(CalendarError, match='Incomplete'):
        dav.listing('https://example.test/calendar/')

def test_source_lock_prevents_concurrent_reconciliation(store, tmp_path):
    path = tmp_path / 'empty.ics'
    path.write_bytes(b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n')
    source = store.add_source('locked', 'file', {'url': str(path)})
    (store.directory / 'locks').mkdir()
    with open(store.directory / 'locks' / source['id'], 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        with pytest.raises(CalendarError, match='already running'):
            store.sync(source['id'], A, B)

def test_configure_and_external_attachment_permission(store, tmp_path):
    source = store.add_source('private', 'feed', {'url': 'https://example.test/token'})
    result = execute(parser().parse_args(['--data', str(store.directory), 'configure', source['id'],
        '--timezone', 'Europe/London', '--past-years', '10']))
    assert result['configured']
    assert store.source(source['id'])['past_years'] == 10
    raw = create('VEVENT', 'x', {'DTSTART': 'DTSTART:20260301T090000Z', 'ATTACH': 'ATTACH:https://example.test/private'})
    path = tmp_path / 'attachment.ics'
    path.write_bytes(raw)
    with pytest.raises(CalendarError, match='requires --fetch'):
        execute(parser().parse_args(['attachment', str(path), 'x', '--output', str(tmp_path / 'out')]))
    assert not (tmp_path / 'out').exists()
