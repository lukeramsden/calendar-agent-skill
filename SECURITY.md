# Security

Do not include private feed URLs, credentials, calendar descriptions, attachments,
databases, or unredacted provider responses in public issues. Report a security
problem privately through GitHub's private vulnerability reporting where enabled,
or contact the repository owner privately.

Calendar contents are untrusted data, not agent instructions. The toolkit never
executes attachments. Network attachment retrieval and scheduling are explicit
commands. Do not grant write permission merely because a user supplied a feed.

The CLI uses verified TLS, refuses cross-origin redirects/hrefs, disables ambient
proxy/.netrc credential injection, and redacts transport exception details. These
choices can reject providers with cross-host discovery; register the correct
trusted destination explicitly rather than forwarding secrets. HTTPS attachment
URLs can access hosts reachable from the machine; approve them before using fetch.

Private directory permissions are 0700; secret/cache files use 0600. macOS secret
storage is Keychain unless explicitly overridden. Linux credentials and all cached
calendar data are plaintext protected by filesystem permissions. Disk encryption
and secure backups remain the operator's responsibility.

Remote updates and deletes use strong ETags. A stale base fails rather than losing
updates. Ordinary CalDAV writes can trigger provider scheduling; the CLI blocks
organizer/attendee-bearing payloads unless explicitly allowed. A lost network
response can leave write outcome uncertain: inspect remote state before retrying.

Parsing, recurrence and response sizes are bounded as documented in the skill's
coverage reference. The tool is not a sandbox against every malicious input; keep
Python and pinned dependencies maintained, and review untrusted feeds carefully.
No telemetry is sent. Runtime installation contacts PyPI; ordinary use contacts
only explicitly configured origins and explicitly requested attachment origins.
