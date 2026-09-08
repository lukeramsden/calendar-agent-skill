import pytest
from calendar_cli.documents import parse_bound, CalendarError
from calendar_cli.store import holes

A, B = parse_bound('2026-03-01'), parse_bound('2026-04-01')

def register(store, tmp_path, basic, name='test'):
    path = tmp_path / (name + '.ics')
    path.write_bytes(basic)
    source = store.add_source(name, 'file', {'url': str(path)})
    return source, path

def test_sync_search_coverage_offline(store, tmp_path, basic):
    source, path = register(store, tmp_path, basic)
    result = store.sync(source['id'], A, B)
    cid = result['calendars'][0]
    assert not store.coverage(cid, A, B)['gaps']
    assert len(store.search('release')) == 3
    assert store.agenda(A, B)['complete']
    assert store.read(store.search('Project')[0]['id'])['component']['type'] == 'VEVENT'
    path.unlink()
    assert store.search('release')
    assert store.agenda(A, B)['occurrences']
    store.sync(source['id'], A, B, fetch=False)

def test_isolation_and_revisions(store, tmp_path, basic):
    source, path = register(store, tmp_path, basic)
    second, _ = register(store, tmp_path, basic, 'other')
    cid = store.sync(source['id'], A, B)['calendars'][0]
    store.sync(second['id'], A, B)
    assert len(store.search('Project')) == 2
    before = store.calendar(cid)['revision']
    draft = store.draft(basic, cid)['draft_id']
    path.write_bytes(basic.replace(b'Project meeting', b'Changed title'))
    store.sync(source['id'], parse_bound('2026-03-10'), B)
    assert store.calendar(cid)['revision'] != before
    assert store.coverage(cid, A, B)['gaps'][0]['end'].startswith('2026-03-10')
    from calendar_cli.documents import parse
    assert store.raw(cid, before) == parse(basic)[0].to_ical()
    assert store.db.execute('SELECT raw FROM feed_snapshots WHERE source_id=? ORDER BY observed_at', (source['id'],)).fetchone()[0] == basic
    assert store.draft_raw(draft) == basic
    assert len(store.search('Project', history=True)) == 2
    assert len(store.search('Project')) == 1

def test_invalid_feed_rollback(store, tmp_path, basic):
    source, path = register(store, tmp_path, basic)
    cid = store.sync(source['id'], A, B)['calendars'][0]
    before = store.calendar(cid)['revision']
    path.write_bytes(b'not ics')
    with pytest.raises(CalendarError):
        store.sync(source['id'], A, B)
    assert store.calendar(cid)['revision'] == before
    assert store.coverage(cid, A, B)['gaps'] == []
    assert store.status()['recent_runs'][0]['status'] == 'failed'

def test_resume_completed_chunks(store, tmp_path, basic, monkeypatch):
    import calendar_cli.store as module
    source, path = register(store, tmp_path, basic)
    original = module.expand
    calls = []
    def fail_second(*args, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise CalendarError('test interruption')
        return original(*args, **kw)
    monkeypatch.setattr(module, 'expand', fail_second)
    stop = parse_bound('2026-06-01')
    with pytest.raises(CalendarError):
        store.sync(source['id'], A, stop)
    cid = store.status()['calendars'][0]['id']
    assert len(store.coverage(cid)['intervals']) == 1
    monkeypatch.setattr(module, 'expand', original)
    store.sync(source['id'], A, stop, fetch=False)
    assert not store.coverage(cid, A, stop)['gaps']
    assert len(store.coverage(cid)['intervals']) == 3

def test_holes():
    assert holes(0, 10, [(0, 2), (1, 4), (6, 7), (7, 8)]) == [(4, 6), (8, 10)]
    assert holes(0, 10, [(-1, 12)]) == []

def test_permissions_and_redaction(store, tmp_path, basic):
    source, _ = register(store, tmp_path, basic)
    assert (store.directory.stat().st_mode & 0o777) == 0o700
    assert ((store.directory / 'calendar.db').stat().st_mode & 0o777) == 0o600
    assert ((store.directory / 'credentials' / (source['id'] + '.json')).stat().st_mode & 0o777) == 0o600
    assert 'url' not in str(store.status())
