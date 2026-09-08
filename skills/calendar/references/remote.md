# Remote operations

Only use these commands for a CalDAV source, after explicit user approval of the
specific remote change. Use a disposable server/calendar for tests.

```bash
calendar-cli discover server
calendar-cli sync server --since 2026-01-01 --until 2027-01-01
calendar-cli resources CALENDAR_ID
calendar-cli remote-query CALENDAR_ID --since 2026-03-01 --until 2026-04-01
calendar-cli remote-put CALENDAR_ID new-event.ics event.ics --create --confirm-remote
calendar-cli remote-put CALENDAR_ID existing.ics edited.ics --etag '"base-etag"' --confirm-remote
calendar-cli remote-delete CALENDAR_ID existing.ics --etag '"base-etag"' --confirm-remote
calendar-cli remote-create-calendar server --home 'https://example.test/user/' --name 'New calendar' --confirm-remote
```

`discover` returns advertised calendar homes, calendar URLs, supported component
types and an outbox where present. These URLs can be private: do not publish them.
Calendar names are labels, not stable identity. Use the local calendar ID returned
by sync. Resource arguments must be plain `.ics` basenames; hrefs outside the
registered origin are rejected. Unusual encoded/path resource names can be read
and synced but are deliberately not exposed through the mutation CLI.

New resources use `If-None-Match: *`. Update/delete use `If-Match` and require a
matching synced base version. Export/query the resource you are editing, retain
its ETag, and review the complete payload. Never use a whole multi-UID calendar
export as one CalDAV event resource. CalDAV resources should hold one UID series
plus any VTIMEZONE definitions. The server may reject unsupported component
combinations, properties, privileges, or calendar creation with a clear HTTP error.

A successful mutation tells you a refresh is required. Run sync and inspect the
result; a PUT response does not guarantee the server retained every property
unchanged. A lost response can leave the outcome unknown. Do not blindly retry a
write or a scheduling request.

## Scheduling

CalDAV servers can send invitations while processing ordinary PUT/DELETE.
The CLI blocks writes involving organizer/attendee-bearing cached or new content
unless `--allow-scheduling` is supplied in addition to `--confirm-remote`. Obtain
permission for this side effect before using that flag. These safeguards are not
a substitute for provider-specific knowledge or testing.

Explicit scheduling uses the advertised outbox:

```bash
calendar-cli schedule CALENDAR_ID request.ics \
  --sender mailto:host@example.test \
  --recipient mailto:guest@example.test --confirm-send
```

Only REQUEST/REPLY/CANCEL documents are accepted. Responses report individual
recipient request statuses; HTTP success alone is not proof of delivery. Servers
without a scheduling outbox fail clearly. Radicale does not provide scheduling;
our real-server tests verify that unsupported case, while a simulated DAV server
covers delivery-response parsing. Provider-specific scheduling remains subject to
its privileges and policy. This tool does not implement SMTP delivery.

## Synchronization

Initial sync discovers calendars and lists their resources. Where a sync token is
available, subsequent sync uses `sync-collection`; unsupported/invalid tokens
fall back to a complete ETag listing. Changed resources are fetched independently,
then a complete calendar snapshot is committed. No missing resource is interpreted
as deleted after a failed/incomplete listing. Explicit remote-query uses a CalDAV
time-range REPORT but does not replace or delete the full cache.
