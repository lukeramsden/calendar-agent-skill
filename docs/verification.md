# Verification evidence

Release verification, 2026-09-09. Version **0.1.0** is published and its registry
artifact has passed installation/workflow checks. The immutable initial package
contains the earlier pre-publication checkpoint; this GitHub document records the
post-publication results.

## Published release

- npm: https://www.npmjs.com/package/calendar-agent-skill/v/0.1.0
- GitHub: https://github.com/lukeramsden/calendar-agent-skill/releases/tag/v0.1.0
- Tag `v0.1.0` points to `633fa3c755a57d2918283eee64d709980f3734df`.
- Registry integrity:
  `sha512-kaXgWrQY7D+tDNXomKvrUwBKARRy/NdY1b+mvCe9yyW11Cabn9S2DOmVCH8GpPYKrm8Pe4MKYwL4TRGMxMGa0g==`.
- Independently downloaded the npm tarball, recomputed its SHA-512 integrity,
  and compared all **21 shipped files byte-for-byte** with the `v0.1.0` Git tag:
  every file matched.
- Release-preparation [CI run 34329330538](https://github.com/lukeramsden/calendar-agent-skill/actions/runs/34329330538)
  passed. The tag's [publish workflow 34329627230](https://github.com/lukeramsden/calendar-agent-skill/actions/runs/34329627230)
  passed tests and correctly detected the already-published version.
- The maintainer completed the initial npm publication with login and 2FA. This
  release does not claim OIDC provenance. Future automated new-version publication
  requires npm trusted-publisher configuration as described in CONTRIBUTING.md.
- `CALENDAR_PACKAGE_SPEC=calendar-agent-skill@0.1.0 sh tests/package-smoke.sh`
  downloaded the real registry artifact and verified isolated installation, locked
  setup, doctor, source registration, sync, FTS and four-occurrence agenda on
  macOS 26. The same registry-artifact test passed in a fresh Debian 13 container
  provisioned with Python/venv, CA certificates, Node and npm.
- `pi install npm:calendar-agent-skill@0.1.0` succeeded in an isolated pi config;
  `pi list` confirmed the exact installed package version.
- GitHub Skills installation was refreshed globally for pi after release;
  `~/.pi/agent/skills/calendar/doctor` passed. Normal user calendar data was not
  changed by installation tests.

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

## Private live-feed integration

On 2026-09-09, the user-supplied feed passed private integration checks: the
configured five-year historical/one-year future window had no processing gaps;
FTS found a known source component; a separate fresh recurrence expansion matched
the cached date-range query; offline queries and cached-only sync succeeded; and
a local property edit left the source snapshot unchanged. No remote mutations or
invitations were sent. URLs, source bytes, event text and detailed source counts
remain outside this repository in protected local storage.

The live source changed between fetches, including export timestamps and initially
additional components. Tests therefore validate each observed revision rather
than incorrectly assuming a live feed is immutable. Historical snapshots were
retained and coverage was recomputed for the new revision. This does not establish
that the provider exposes every historical event. Export-timestamp changes can
currently invalidate occurrence coverage conservatively, making refresh slower
than an unchanged-feed refresh; cached offline queries remain fast.

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
| User feed | **Passed privately, 2026-09-09**: live fetch, backfill coverage, independent recurrence comparison, FTS, offline operation and non-destructive local editing; no private data committed |
| GitHub/npm publication | **Published and verified**: npm 0.1.0 and GitHub v0.1.0 release; exact URLs, commit and registry integrity above |
| Skills/local installation | Refreshed GitHub Skills install and doctor passed; isolated pi npm installation passed; actual npm registry-artifact workflows passed on macOS and Debian |

## Release gate outcome

Private live-feed validation, supported-platform verification, publication and
published-artifact installation checks have passed. No source URL, credential,
calendar text or private integration artifact was included in the release.
Future automated publishing configuration is a maintainer setup step, not a claim
that this manually authenticated first publish had an OIDC attestation.

The feature/standards boundary is explicit in
[coverage.md](../skills/calendar/references/coverage.md). No claim of universal
RFC/provider compatibility or complete omitted upstream history is made.
