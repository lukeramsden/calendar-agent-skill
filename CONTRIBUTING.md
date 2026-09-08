# Development

Python 3.11+; tested targets are macOS 26 and Debian stable. Run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-test.lock
sh tests/run.sh
# npm test / npm run verify invoke the same suite when npm is available.
```

Tests use synthetic calendars, owner-only temporary directories, and disposable
loopback Radicale servers. They never use your actual configured calendar data or
write to a real calendar. CALENDAR_FILE_SECRETS is set only in the test process.

Runtime modules belong under `skills/calendar/calendar_cli/` so copying only the
skill still works. Dependencies belong in `skills/calendar/requirements.in`.
After review, regenerate universal hash locks with uv:

```bash
uv pip compile skills/calendar/requirements.in --universal --python-version 3.11 --generate-hashes -o skills/calendar/requirements.lock
uv pip compile requirements-test.in --universal --python-version 3.11 --generate-hashes -o requirements-test.lock
```

The supported source format is VERSION:2.0 ICS. Keep parsing/recurrence in maintained
libraries; own the protocol safety and transactional cache semantics. The DAV wire
layer is intentionally small and direct so redirect policy and conditional writes
are not hidden behind a provider client's implicit retries or scheduling actions.

Update `docs/verification.md` with observed evidence, not claims. Tests must cover
regressions before changing recurrence identity, deletion, coverage or ETag logic.

## Release

1. Complete the private-feed check and macOS/Debian test matrix.
2. Run `npm pack --dry-run --json`; inspect contents for credentials, caches,
   bytecode and omitted runtime files. Install the packed artifact in a temporary
   npm prefix and run setup, doctor and an offline/local-source workflow.
3. Ensure the initial npm package exists and configure npm trusted publishing for
   `lukeramsden/calendar-agent-skill`, workflow `publish.yml`. The first publish may
   require an interactive npm login/OTP. Never put a token in source control.
4. Update release status and evidence, set the package version, commit, and tag
   `v<version>`. The publish workflow verifies tests before npm publishing with
   OIDC/provenance. Push the tag only after publishing authorization is ready.
5. Install from the published npm version and GitHub Skills path; run smoke tests.
   Record the URLs, version, CI runs and test results. Install the skill locally.

Do not call a release complete while user-feed verification or publication is
blocked. Do not attach private integration artifacts to a release.
