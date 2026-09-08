import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from calendar_cli.cli import execute, parser, scheduling_guard, busy_intervals
from calendar_cli.documents import CalendarError, create, parse_bound

ROOT = Path(__file__).resolve().parents[1]

def invoke(tmp_path, *args, ok=True):
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'skills/calendar'), CALENDAR_DATA=str(tmp_path / 'cli-data'), CALENDAR_FILE_SECRETS='1')
    p = subprocess.run([sys.executable, '-m', 'calendar_cli.cli', *args], capture_output=True, text=True, env=env, timeout=40)
    if ok:
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)
    assert p.returncode != 0
    return p

def test_cli_end_to_end(tmp_path, basic):
    feed = tmp_path / 'example.ics'
    feed.write_bytes(basic)
    config = tmp_path / 'source.json'
    config.write_text(json.dumps({'url': str(feed)}))
    assert invoke(tmp_path, 'doctor')['ok']
    invoke(tmp_path, 'add', 'example', '--kind', 'file', '--config', str(config))
    result = invoke(tmp_path, 'sync', 'example', '--since', '2026-03-01', '--until', '2026-04-01')
    cid = result['results'][0]['calendars'][0]
    assert invoke(tmp_path, 'search', 'release', '--no-sync')['results']
    agenda = invoke(tmp_path, 'agenda', '--since', '2026-03-01', '--until', '2026-04-01', '--no-sync')
    assert agenda['complete']
    assert any(o['uid'] == 'task' for o in agenda['occurrences'])
    output = tmp_path / 'output.ics'
    invoke(tmp_path, 'export-source', 'example', '--output', str(output))
    assert output.read_bytes() == basic
    draft = invoke(tmp_path, 'import', str(feed), '--calendar', cid)['draft_id']
    properties = tmp_path / 'patch.json'
    properties.write_text(json.dumps({'SUMMARY': 'SUMMARY:Changed locally'}))
    invoke(tmp_path, 'edit', draft, 'meeting', '--properties', str(properties))
    edited = tmp_path / 'edited.ics'
    invoke(tmp_path, 'draft-export', draft, '--output', str(edited))
    assert b'Changed locally' in edited.read_bytes()
    assert feed.read_bytes() == basic
    assert invoke(tmp_path, 'validate', str(edited))['valid']
    assert invoke(tmp_path, 'diff', str(feed), str(edited))['diff']
    busy = invoke(tmp_path, 'freebusy', '--since', '2026-03-01', '--until', '2026-03-02', '--no-sync')
    assert any(r['start'].startswith('2026-03-01T15:00') for r in busy['busy'])
    feed.unlink()
    assert invoke(tmp_path, 'search', 'release', '--no-sync')['results']
    error = invoke(tmp_path, 'remote-delete', cid, 'meeting.ics', ok=False)
    assert 'confirm' in error.stderr
    assert 'Traceback' not in error.stderr

def test_no_overwrite(tmp_path, basic):
    feed = tmp_path / 'example.ics'
    feed.write_bytes(basic)
    p = invoke(tmp_path, 'invitation', str(feed), '--method', 'PUBLISH', '--output', str(feed), ok=False)
    assert 'already exists' in p.stderr
    assert feed.read_bytes() == basic

def test_scheduling_guard():
    raw = create('VEVENT', 'x', {'DTSTART': 'DTSTART:20260301T090000Z', 'ORGANIZER': 'ORGANIZER:mailto:test@example.test'})
    with pytest.raises(CalendarError):
        scheduling_guard(raw, False)
    scheduling_guard(raw, True)

def test_attachment(tmp_path):
    raw = create('VEVENT', 'x', {'DTSTART': 'DTSTART:20260301T090000Z', 'ATTACH': 'ATTACH;VALUE=BINARY;ENCODING=BASE64:aGVsbG8='})
    file = tmp_path / 'attach.ics'
    file.write_bytes(raw)
    output = tmp_path / 'attachment.txt'
    invoke(tmp_path, 'attachment', str(file), 'x', '--output', str(output))
    assert output.read_bytes() == b'hello'
