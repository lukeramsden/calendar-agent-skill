#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT HUP INT TERM
if [ -n "${CALENDAR_PACKAGE_SPEC:-}" ]; then
    npm pack "$CALENDAR_PACKAGE_SPEC" --pack-destination "$WORK" --json > "$WORK/pack.json"
else
    npm pack --pack-destination "$WORK" --json > "$WORK/pack.json"
fi
PYTHON=${CALENDAR_TEST_PYTHON:-.venv/bin/python}
PACKAGE=$("$PYTHON" -c 'import json,sys; p=json.load(open(sys.argv[1]))[0]; paths=[f["path"] for f in p["files"]]; assert not any("__pycache__" in s or ".private" in s or s.endswith((".db",".pyc")) for s in paths); assert "skills/calendar/calendar_cli/store.py" in paths; print(p["filename"])' "$WORK/pack.json")
npm install --prefix "$WORK/install" --ignore-scripts "$WORK/$PACKAGE" >/dev/null
export CALENDAR_RUNTIME="$WORK/runtime"
export CALENDAR_DATA="$WORK/data"
export CALENDAR_FILE_SECRETS=1
BIN="$WORK/install/node_modules/.bin/calendar-cli"
"$BIN" setup
"$BIN" doctor
"$PYTHON" -c 'import json,sys; print(json.dumps({"url":sys.argv[1]}))' "$PWD/tests/fixtures/sample.ics" > "$WORK/source.json"
"$BIN" add demo --kind file --config "$WORK/source.json"
"$BIN" sync demo --since 2026-10-01 --until 2026-11-01
"$BIN" search planning --no-sync > "$WORK/search.json"
"$PYTHON" -c 'import json,sys; assert len(json.load(open(sys.argv[1]))["results"])==1' "$WORK/search.json"
"$BIN" agenda --since 2026-10-01 --until 2026-11-01 --no-sync > "$WORK/agenda.json"
"$PYTHON" -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["complete"] and len(r["occurrences"])==4' "$WORK/agenda.json"
printf '%s\n' 'Artifact install, setup, doctor, source sync, FTS and agenda: passed'
