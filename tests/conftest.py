import os
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/calendar'))
os.environ['CALENDAR_FILE_SECRETS'] = '1'
from calendar_cli.store import Store

@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'private')
    yield s
    s.db.close()

@pytest.fixture
def basic():
    return b'''BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Tests//EN\r
X-WR-CALNAME:Example\r
X-UNKNOWN;X-PARAM=retained:vendor-value\r
BEGIN:VEVENT\r
UID:meeting\r
DTSTAMP:20260101T000000Z\r
DTSTART:20260301T100000Z\r
DTEND:20260301T110000Z\r
RRULE:FREQ=DAILY;COUNT=4\r
EXDATE:20260302T100000Z\r
SUMMARY:Project meeting\r
DESCRIPTION:Planning the release\r
LOCATION:Office\r
X-CUSTOM;X-ARG=yes:keep me\r
BEGIN:VALARM\r
ACTION:DISPLAY\r
TRIGGER:-PT15M\r
DESCRIPTION:Reminder\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:meeting\r
RECURRENCE-ID:20260303T100000Z\r
DTSTAMP:20260101T000000Z\r
DTSTART:20260303T120000Z\r
DTEND:20260303T130000Z\r
SUMMARY:Moved meeting\r
END:VEVENT\r
BEGIN:VTODO\r
UID:task\r
DTSTAMP:20260101T000000Z\r
DUE:20260305T120000Z\r
SUMMARY:Ship release\r
STATUS:NEEDS-ACTION\r
END:VTODO\r
BEGIN:VJOURNAL\r
UID:journal\r
DTSTAMP:20260101T000000Z\r
DTSTART;VALUE=DATE:20260301\r
SUMMARY:Release notes\r
END:VJOURNAL\r
BEGIN:VFREEBUSY\r
UID:busy\r
DTSTAMP:20260101T000000Z\r
FREEBUSY:20260301T150000Z/20260301T160000Z\r
END:VFREEBUSY\r
END:VCALENDAR\r
'''
