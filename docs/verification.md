# Verification evidence

Development checkpoint, 2026-09-08. No release is claimed complete.

## Observed runs

- macOS 26.6.2 (25G83), arm64, Python 3.14.3 / SQLite 3.51.3:
  `.venv/bin/python -m pytest tests -q`: **35 passed**, 10.13 seconds.
- Final [CI run 34254966857](https://github.com/lukeramsden/calendar-agent-skill/actions/runs/34254966857),
  tested commit `6aae678`, **all jobs passed**:
  - Fresh macOS 26 runner, Python 3.14.7 / SQLite 3.50.4: **35 passed**, 19.01s;
    clean runtime and packed npm installation smoke passed.
  - Fresh Debian 13 stable-slim container, Python 3.13.5 / SQLite 3.46.1:
    **35 passed**, 10.67s; apt prerequisites, locked install, setup and doctor passed.
  - Ubuntu, Python 3.11.16 / SQLite 3.45.1: **35 passed**, 13.64s;
    clean runtime and packed npm installation smoke passed.
- Earlier macOS CI failures were diagnosed with a child-process stack trace:
  Radicale's HTTPServer startup blocked in reverse DNS (`socket.getfqdn`). The
  synthetic loopback server now uses a fixed localhost display name. The calendar
  client and real DAV behavior are not stubbed; the full protocol tests pass.
- `sh tests/package-smoke.sh`: npm-packed artifact installed into an isolated
  prefix; clean locked runtime setup, doctor, source registration, sync, FTS and
  four-occurrence agenda all passed. No bytecode/database/private paths in artifact.
  This is a local packed-artifact test, not an npm-registry release test.
- macOS Keychain: synthetic set/get/delete succeeded, and the test secret was
  removed. Automated suite uses protected temporary file credentials.
- `pytest tests/test_edges.py::test_search_performance -q -s`: 10,000 synthetic
  components, 1,000 FTS matches in **0.0056 seconds** on the Mac. Not a benchmark
  of the user's unknown feed or every archive size.

## Requirement matrix

| Requirement | Evidence / remaining work |
| --- | --- |
| Architecture, distribution shape | docs/design.md; self-contained skills/calendar; hash locks; npm manifest |
| Clean macOS / Debian runtime | Setup and doctor passed on real Mac and fresh Debian container; macOS OS itself was not reinstalled |
| Multiple sources/calendars | test_store isolation; test_documents named multi-container identity; ambiguous anonymous streams rejected |
| Private config | permission/redaction tests, cross-origin redirect test, real Keychain synthetic test |
| Original and unknown preservation | feed_snapshots/resource_history; top-level and nested property round-trip tests; drafts survive refresh |
| Core component kinds | synthetic VEVENT, VTODO, VJOURNAL, VFREEBUSY, VALARM and VTIMEZONE fixtures |
| Time and recurrence | floating DST, all-day exclusive overlap, RRULE/RDATE/EXDATE, moved/cancelled overrides, THISANDFUTURE, custom TZID |
| Local operations | CLI import/edit/export, creation, component deletion with retained draft history, validation, diff, refusal to overwrite files; test_cli and test_regressions |
| Feed sync | HTTP 200/304, changed recurrence revision, malformed rollback, separate historical observations |
| Coverage/resume | explicit windows, revision invalidation, gap subtraction, interrupted chunks retained/resumed; failed attempt records |
| FTS/offline | component fields/phrases, history, identity isolation, subprocess CLI offline query; 10k-component measured benchmark |
| Agenda/conflicts/freebusy | agenda, explicit VFREEBUSY, overlapping events, transparent/cancelled exclusions and merged busy intervals tested |
| CalDAV read | disposable Radicale discovery, date REPORT, full/ETag sync, token sync, invalid token fallback; simulated unsupported-token REPORT fallback |
| CalDAV write | real disposable create/update/delete/calendar creation and stale ETag conflicts; CLI guards for consent and scheduling |
| Scheduling | invitation/reply payload fixtures, unsupported Radicale outbox rejection, simulated per-recipient success/failure response parsing; no real invitations sent |
| Attachments | binary extraction tested; external fetch requires explicit flag; bounded HTTPS transport shared with feeds |
| Limits/safety | response/document/worker/output bounds, XML entity rejection, no cross-origin credential forwarding; limitations documented |
| User feed | **Pending: user has not supplied it**; no private data included in fixtures |
| GitHub/npm publication | Public repository at https://github.com/lukeramsden/calendar-agent-skill; CI passed; **npm whoami returns E401**; npm publication and release/tag remain pending |
| Skills/local installation | GitHub Skills discovery/install passed; installed calendar skill at ~/.pi/agent/skills/calendar and its doctor passed; packed npm CLI installation passed |

## Release gates still open

1. Receive and privately test the user-supplied ICS feed.
2. Resolve initial npm publishing authentication or trusted-publisher configuration.
3. Publish a release/tag and verify the actual npm-registry artifact. GitHub source
   distribution and local skill installation are already verified; refresh the local
   installation after release.

The feature/standards boundary is explicit in
[coverage.md](../skills/calendar/references/coverage.md). No claim of universal
RFC/provider compatibility or complete omitted upstream history is made.
