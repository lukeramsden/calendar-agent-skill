# Calendar skill design

Status: implementation architecture; release verification remains in verification.md.

## Interface and packaging

Repository/npm package: `calendar-agent-skill`. Skill: `calendar`.
CLI: `calendar-cli` (avoid the system `calendar` command). All structured data
is JSON on stdout; errors and progress go to stderr. Raw ICS/export commands
are explicit exceptions. Ship the complete runtime under `skills/calendar/`
so Skills CLI installation does not lose code outside the skill directory.
No npm postinstall execution. An explicit setup command installs an isolated,
locked Python environment; doctor diagnoses missing prerequisites without
modifying the host. Document fresh macOS and Debian bootstrap separately.

Use maintained `icalendar` and `recurring-ical-events` libraries for calendar
semantics. DAV uses a small explicit requests/defusedxml layer instead of the
initially considered `caldav` client: redirect policy, conditional mutations and
scheduling side effects need to remain under this tool's control. Runtime and
test dependency closures are universal, version/hash-locked. SQLite FTS5 is
checked at setup. No system pip installs.

## Module boundaries

- Calendar documents: parsing, validation, structured properties, serialization,
  recurrence, and local mutation. Preserve original bytes separately from parsed
  serialization; unknown properties and parameters must survive edits.
- Store: transactions, schema migrations, snapshots, occurrence intervals, FTS,
  and local drafts. No network operations inside database transactions.
- Sources: bounded HTTP reads, validators, CalDAV discovery and capabilities,
  conditional writes, credentials and safe error reporting.
- CLI: argument validation, explicit side-effect consent, JSON contracts.

Avoid a shared abstraction that pretends HTTP snapshots and CalDAV object
collections have identical deletion or history semantics.

## Identities and persistence

Sources have local stable IDs. CalDAV calendar identity is its canonical href;
ICS containers use explicit calendar UID when supplied, otherwise a persisted
mapping with ambiguity detection (container ordering alone is not identity).
Components are scoped to calendar, UID, component type and RECURRENCE-ID.
Missing or duplicate identifiers must be reported, not silently overwritten.

Tables: sources, feeds, feed_snapshots, calendars, snapshots, resources,
resource_history, components, occurrences, coverage, expansion_attempts,
sync_runs, drafts, mutation_history, and component_fts.
Preserve raw source snapshots and prior resource revisions. Source URLs and
calendar data are private. Database and directories are owner-only. Secret
references, not passwords, belong in source metadata; macOS Keychain and a
protected Linux credential file are the initial platform backends.

Local edits live in drafts/local calendars, never masquerading as source state.
CalDAV updates require the version/ETag they were based on; conflicts retain
both versions and do not retry blindly. Creates use If-None-Match: *. Deletes
require a known ETag and explicit invocation. Test writes use disposable servers.

## Time and coverage

Windows are half-open [start, end); queries use overlap rather than DTSTART-only
filtering. Preserve DATE versus DATE-TIME, TZID and floating-time semantics.
Use an explicit calendar/query timezone for floating times, never silently
interpret them as UTC. All-day DTEND is exclusive. Recurrence expansion is
bounded by time and occurrence count; exceeding a bound fails the interval.

Retain all available source definitions regardless of expansion window. Default
occurrence window is five calendar years back and one ahead. Explicit ranges
can fill holes; checkpoint chunks transactionally with occurrence data. Coverage
records calendar/source revision, bounds, timezone, expansion engine version,
completion timestamp and outcome. Merge only compatible adjacent intervals.
A fetch or partial expansion cannot claim completed coverage. A new revision
invalidates affected intervals; a 304 can still require expansion into new
windows. Completed intervals describe processing of available data, not proof
that the upstream source includes every historical event.

A feed removal means absent in the current snapshot, not confirmed cancellation.
Keep prior records separately visible as historical observations. CalDAV deletion
is authoritative only within a successful complete listing or valid sync-token
response. Invalid sync tokens trigger full reconciliation, not mass deletion.

## Safety and capability boundaries

Verified TLS by default. Explicit insecure HTTP opt-in except disposable loopback
tests. Never forward credentials or secret query parameters to a different origin.
Bound response size, redirects, request timeouts, expansion and attachment size.
Do not fetch attachment URLs or send scheduling messages during ordinary sync.
Treat ICS descriptions and attachments as untrusted content, not instructions.
Redact URLs and transport exception text from normal diagnostics.

ICS subscription URLs do not support remote edits. CalDAV capabilities vary;
discover and test before enabling calendar creation or scheduling. Invitation
payload export is distinct from sending a scheduling request. No OS alarm daemon,
GUI, hosted service, or proprietary provider APIs.

## Release evidence

Do not release on unit tests alone. Verify clean setup on macOS 26 and Debian,
a disposable CalDAV server, the private user feed, and installation from packed
and published npm artifacts. Record exact commands, versions, failures and fixes.
Never commit the private feed, URLs, credentials, caches or integration output.
