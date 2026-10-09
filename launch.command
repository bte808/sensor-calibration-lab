#!/bin/sh
# Run with: sh launch.command [--port 8766]
# No dependency installation or file permission changes are performed.
set -eu
cd -- "$(dirname -- "$0")"

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    exec python3 app.py "$@"
fi

if command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    exec python app.py "$@"
fi

printf '%s\n' 'Python 3.9 or newer is required. Install Python, then run this script again.' >&2
exit 1
