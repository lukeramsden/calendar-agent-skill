#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PYTHON=${CALENDAR_TEST_PYTHON:-.venv/bin/python}
if [ ! -x "$PYTHON" ]; then
    echo 'Create .venv and install requirements-test.lock first (see CONTRIBUTING.md).' >&2
    exit 1
fi
"$PYTHON" -m compileall -q skills/calendar/calendar_cli
"$PYTHON" -m pytest tests -q
