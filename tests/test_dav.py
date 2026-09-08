"""Real disposable CalDAV integration. Never contacts a user's calendar service."""
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

from calendar_cli.sources import DAVClient, CalendarError
from calendar_cli.documents import create, parse_bound, patch

@pytest.fixture
def dav(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    cfg = tmp_path / 'radicale.conf'
    cfg.write_text(f'''[server]
hosts = 127.0.0.1:{port}
[auth]
type = none
[rights]
type = owner_only
[storage]
filesystem_folder = {tmp_path / 'radicale-data'}
[logging]
level = error
''')
    log = open(tmp_path / 'radicale.log', 'wb')
    bootstrap = "import faulthandler,runpy; faulthandler.dump_traceback_later(20, repeat=True); runpy.run_module('radicale', run_name='__main__')"
    process = subprocess.Popen([sys.executable, '-c', bootstrap, '--config', str(cfg)], stdout=log, stderr=log)
    url = f'http://127.0.0.1:{port}/'
    readiness = requests.Session()
    readiness.trust_env = False
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                readiness.get(url, timeout=.2)
                break
            except requests.RequestException:
                if process.poll() is not None:
                    pytest.fail('Disposable Radicale failed to start: ' + (tmp_path / 'radicale.log').read_text())
                time.sleep(.05)
        else:
            pytest.fail('Disposable Radicale readiness timeout: ' + (tmp_path / 'radicale.log').read_text())
        client = DAVClient({'url': url, 'username': 'test', 'password': 'test', 'allow_http': True})
        client.make_calendar(url + 'test/calendar/', 'Integration')
        yield client
    finally:
        readiness.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def test_discovery_crud_etags_query(dav):
    calendars = dav.discover()
    assert len(calendars) == 1
    assert calendars[0]['name'] == 'Integration'
    url = calendars[0]['href']
    resource = url + 'one.ics'
    raw = create('VEVENT', 'one', {'SUMMARY': 'SUMMARY:Integration', 'DTSTART': 'DTSTART:20260301T090000Z', 'DTEND': 'DTEND:20260301T100000Z'})
    assert dav.put(resource, raw)['written']
    with pytest.raises(CalendarError, match='conflict'):
        dav.put(resource, raw)
    read, etag = dav.get(resource)
    assert b'Integration' in read
    rows = dav.query(url, parse_bound('2026-03-01'), parse_bound('2026-04-01'))
    assert len(rows) == 1
    changed = patch(read, 'one', '', {'SUMMARY': 'SUMMARY:Updated'})
    dav.put(resource, changed, etag)
    with pytest.raises(CalendarError, match='conflict'):
        dav.put(resource, raw, etag)
    with pytest.raises(CalendarError, match='conflict'):
        dav.delete(resource, etag)
    _, latest = dav.get(resource)
    dav.delete(resource, latest)
    assert dav.listing(url)[0] == []
    assert calendars[0]['outbox'] is None
    with pytest.raises(CalendarError):
        dav.schedule(None, raw, 'mailto:a@example.test', ['mailto:b@example.test'])


def test_store_incremental_and_deletion(store, dav):
    url = dav.discover()[0]['href']
    raw = create('VEVENT', 'one', {'SUMMARY': 'SUMMARY:Cached', 'DTSTART': 'DTSTART:20260301T090000Z'})
    dav.put(url + 'one.ics', raw)
    source = store.add_source('dav', 'caldav', {'url': dav.http.url, 'username': 'test', 'password': 'test', 'allow_http': True})
    a, b = parse_bound('2026-03-01'), parse_bound('2026-04-01')
    cid = store.sync(source['id'], a, b)['calendars'][0]
    token = store.calendar(cid)['sync_token']
    assert token
    assert len(store.search('Cached')) == 1
    store.sync(source['id'], a, b)
    _, etag = dav.get(url + 'one.ics')
    dav.delete(url + 'one.ics', etag)
    store.sync(source['id'], a, b)
    assert not store.search('Cached')
    assert len(store.search('Cached', history=True)) == 1
    assert store.calendar(cid)['sync_token'] != token
    # A bad token must reconcile fully, without treating token failure as deletion.
    with store.db:
        store.db.execute('UPDATE calendars SET sync_token=? WHERE id=?', ('invalid', cid))
    store.sync(source['id'], a, b)
    assert not store.search('Cached')
