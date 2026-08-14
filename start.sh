#!/bin/sh
set -e

# Start the PO token provider in the background. If this fails to start for
# any reason, downloads still work — yt-dlp just won't have a token source,
# which is the same as before this was added, not a new failure mode.
bgutil-pot server --host 127.0.0.1 --port 4416 &

exec gunicorn -w 1 -b "0.0.0.0:${PORT:-10000}" app:app
