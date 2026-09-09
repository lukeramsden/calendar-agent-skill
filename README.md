# Calendar Agent Skill

A Python calendar toolkit for agents: multiple ICS feeds, local calendar editing,
CalDAV discovery and safe remote writes, with SQLite FTS5 search and resumable
historical backfill. Same skill layout and JSON-first CLI pattern as the Gmail
and Proton Mail agent skills.

**Initial version: 0.1.0.** Private live-feed integration and the cross-platform
test suite have passed. See the [current release evidence](https://github.com/lukeramsden/calendar-agent-skill/blob/main/docs/verification.md)
for registry publication and post-release checks.

## Install

Install the skill from GitHub:

```bash
npx skills add lukeramsden/calendar-agent-skill@calendar
```

Or install through npm / pi:

```bash
pi install npm:calendar-agent-skill
npm install -g calendar-agent-skill
```

For a source checkout, enter `skills/calendar/` and run `./setup`, then
`./calendar-cli --help`. npm installs do not execute lifecycle scripts. No Node.js
runtime is needed to use the CLI from an existing checkout/download; npm, pi and
the Skills CLI are optional distribution paths, with their own prerequisites.

### Fresh macOS 26

1. Install a current **signed universal Python 3.11+ installer** from
   <https://www.python.org/downloads/macos/>. This can require administrator access.
   Do not assume a new Mac includes usable Python or command-line developer tools.
2. Download and extract the repository/release archive (Git and Homebrew are not
   needed), or use one of the package installation paths above if already available.
3. In Terminal, run `sh /path/to/skills/calendar/setup`.
4. Run `/path/to/skills/calendar/doctor`.

Set `CALENDAR_PYTHON` to the full Python executable path if it is not on PATH.
To use npm distribution on a fresh Mac, separately install Node.js LTS using its
signed installer. The calendar runtime itself does not require Node.

### Fresh Debian stable

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv ca-certificates
# Download/extract the skill or use an existing checkout, then:
sh skills/calendar/setup
skills/calendar/doctor
```

Provisioning OS prerequisites needs root/admin access. Setup needs HTTPS access to
PyPI; feed/CalDAV sync needs network access to the chosen server. Dependencies are
version- and hash-locked and installed in a private virtual environment, never via
system pip. No compiler is required for the supported runtime dependencies.

## Quick start

Put source details in an owner-only JSON file, not command-line arguments:

```json
{"url":"https://example.test/private/calendar.ics"}
```

```bash
calendar-cli add work --kind feed --config /private/source.json --timezone Europe/London
calendar-cli sync work --since 2021-01-01 --until 2027-01-01
calendar-cli calendars
calendar-cli coverage CALENDAR_ID --since 2021-01-01 --until 2027-01-01
calendar-cli search 'planning OR release' --no-sync
calendar-cli agenda --since 2026-10-01 --until 2026-11-01
calendar-cli list --kind VTODO
```

For CalDAV choose `--kind caldav` and include `username`/`password` or `bearer` in
the private JSON. For local files choose `--kind file` with an absolute path as
`url`. `webcal://` uses HTTPS. Plain HTTP needs an explicit `allow_http: true`.
After registration the URL and credentials are stored privately; securely remove
your temporary config if no longer needed.

## What it does

- Multiple sources with separate calendar identities and component UID namespaces.
- Original ICS snapshots, parsed components, historical revisions, local drafts,
  full-text search, occurrence indexes and revision-aware coverage in SQLite/WAL.
- Default five-year backfill and one-year future expansion, configurable per source;
  explicit date windows, gap detection and transactionally resumable chunk processing.
- Cache-first listing/search/agenda, offline reads, freshness reporting, conflicts
  and free/busy. Feed HTTP validators and CalDAV sync-token/ETag reconciliation.
- ICS events, tasks, journals, free/busy, timezones, alarms, attendees and attachments;
  recurrence, exclusions, overrides, floating times, all-day dates and DST.
- Inspect/validate/diff/import/export, local component creation/editing/deletion,
  unknown-property retention, and typed arbitrary property editing.
- CalDAV discovery, date-range queries, calendar creation, conditional resource
  create/update/delete and explicit capability-gated scheduling.
- Read-only-by-default workflows, private URL redaction, safe redirects, bounded
  parsing/expansion and explicit remote/scheduling consent flags.

The 35-test suite passes on macOS 26, Debian 13, and Python 3.11 Linux CI,
including a disposable real CalDAV server and fresh packed npm installation.

The full agent command guide is [SKILL.md](skills/calendar/SKILL.md).
See [remote writes](skills/calendar/references/remote.md) and
[coverage/limitations](skills/calendar/references/coverage.md) before relying on
calendar completeness or writing to a live calendar.

## Important distinctions

**ICS is not a history-query or write protocol.** A feed cannot provide events the
publisher omitted. Coverage means successfully processing the available source,
not proof of complete upstream history. CalDAV can expose writable resources and
time-range queries, subject to server support.

**Drafts do not change upstream.** Remote operations are separate and require
explicit flags. Attendee-bearing CalDAV writes can send invitations, so they need
additional scheduling permission. Invitation export does not send mail; VALARM
storage does not schedule an operating-system alarm.

**Broad ICS support is not every optional RFC behavior.** Validation is structural,
not full standards certification. Unknown properties are retained; optional
behavior such as non-Gregorian recurrence or VAVAILABILITY computation is not
claimed. See the tested feature matrix and limitations rather than assuming
universal provider compatibility.

## Privacy and storage

Default data: `~/.local/share/calendar-agent-skill/` (respects XDG_DATA_HOME), or
`CALENDAR_DATA`. Runtime: its `runtime/` subdirectory, or `CALENDAR_RUNTIME`.
macOS uses Keychain for private URLs and credentials. Linux uses owner-only files.
`CALENDAR_FILE_SECRETS=1` explicitly selects owner-only files on macOS for headless
or test use. Cache files contain private calendar data and are not encrypted at rest.

Unlike a disposable email cache, this database includes drafts and historical
source versions that may no longer exist upstream. **Back it up securely; do not
assume it can be rebuilt in full.** See [SECURITY.md](SECURITY.md).

## Development and release

See [CONTRIBUTING.md](CONTRIBUTING.md) for locked test setup and cross-platform CI.
The repository uses MIT licensing. Third-party dependencies retain their licenses;
see [NOTICE](NOTICE).
