# Coverage and standards limits

## What a completed interval means

Coverage records a calendar revision, interpretation timezone, recurrence-engine
version, half-open start/end, and completion time. It certifies that the available
source was processed for that interval. It never certifies that a provider exposes
all historical events.

All available source definitions are retained, even outside the chosen window.
Finite occurrence expansion defaults to five calendar years before now through one
year after now. Source registration accepts `--past-years` / `--future-years`;
sync accepts explicit `--since` / `--until`. Expansion checkpoints in at most
31-day chunks. Completed chunks survive errors or interruption. Rerunning sync
subtracts completed intervals and fills holes; `sync --cached` works without
fetching. A 304 does not prevent expanding a new window.

Changing a source creates a new normalized revision. Old occurrences and coverage
are retained as history but excluded from current queries. Only successfully
expanded intervals of the new revision count as complete. This deliberately
invalidates the whole calendar's old coverage rather than guessing which complex
recurrence edits were harmless. Failed/running attempts are reported separately;
a terminated process can leave a running attempt as an audit record, not coverage.
A subsequent successful attempt can cover the same interval.

`agenda.complete` means all selected cached active calendars have processed the
requested interval. Check source freshness and refresh warnings separately.
No registered calendars returns incomplete, not an empty-but-complete world.
Explicitly selecting an unknown/inactive calendar must not be mistaken for a
complete empty result. Search indexes all cached component definitions; date-range
queries use occurrences. Backfill cannot recover past versions never observed.

## Source changes and identity

ICS feeds normally supply current full snapshots. Preserve the exact original
bytes in feed history. A component missing from a later snapshot is absent from
that revision, not necessarily cancelled. Search `--history` includes previous
observations. Local drafts and their edit history live separately.

CalDAV calendars use their canonical href as source identity. ICS containers use
calendar UID, a single-container default identity, or unique names in a multi-
container stream. Ambiguous anonymous containers are rejected. Renaming a named
anonymous container in a multi-calendar stream cannot be proven to preserve
identity and is treated as a new calendar. UIDs never merge across calendars.

## Supported surface and explicit limits

- Parse, inspect, retain and export VEVENT, VTODO, VJOURNAL, VFREEBUSY, VTIMEZONE,
  VALARM, arbitrary extension properties and parameters. Edit top-level component
  properties through typed content lines; structural/nested editing uses raw ICS
  export/import. Original byte preservation and normalized round-trip preservation
  are distinct: normalized exports can reorder/fold lines.
- Expand events, date-bearing tasks and journals using `recurring-ical-events`.
  RRULE/RDATE/EXDATE, recurrence overrides and timezone transitions use that
  library's semantics. Time-free tasks/journals remain searchable but have no
  agenda occurrence. Do not claim arbitrary vendor recurrence extensions work.
- Validation checks calendar version, parse errors, known date/time TZID
  resolution, required component UID and duplicate identity. It is not a complete
  RFC 5545/5546 conformance validator; provider validation can be stricter.
- Conflict checks compare opaque non-cancelled events. Free/busy merges those
  events and explicit non-FREE VFREEBUSY periods; it does not infer office hours,
  travel time, attendee availability, resource bookings, or scheduling privileges.
- Cancelled occurrences can appear in agenda with their status but do not occupy
  busy time. Attendee PARTSTAT does not automatically cancel the organizer's event.
- Documents/responses are capped at 32 MiB. Occurrence expansion runs in a killable
  subprocess with 30-second wall and 25-second CPU limits and 50,000 returned
  occurrences per chunk. Linux also caps worker virtual memory at 768 MiB. macOS
  has no equivalent reliable address-space cap in this implementation. Request a
  smaller explicit window for dense rules. The dependency can allocate intermediate
  occurrences before the returned-count check, so CPU/wall limits remain essential.
- Requests use connection/read timeouts, a response-size bound, verified TLS,
  no ambient proxy/.netrc credentials, and at most five same-origin redirects.
  Read timeout is an inactivity timeout, not a total transfer deadline.
- No VAVAILABILITY calculation, non-Gregorian recurrence engine, SMTP transport,
  OS alarm scheduling, attachment execution, provider-specific API, or GUI.
  Such components/properties can be retained and edited as ICS without claiming
  their operational semantics are implemented.

The aim is broad, inspectable ICS access, not a claim that every optional RFC and
vendor behavior can be executed. Unsupported or malformed operations should fail
clearly and must never acquire coverage or silently overwrite remote state.
