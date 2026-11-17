#!/bin/bash
# star-gaze preview — runs the shader saver without touching idle settings.
# Usage: preview.sh [seconds]   (0 or omitted = run until dismissed)
# Dismiss: any key (star-gaze submap catchall), switch workspace, or lock.
set -u

HERE="$(dirname "$(readlink -f "$0")")"
SECS="${1:-0}"

MARKER="/tmp/star-gaze-preview.pid"

# One saver at a time: a manual preview replaces a running instance.
pkill -f '[s]tar-gaze[.]py' 2>/dev/null || true

# Tell the idle supervisor a manual saver is up, so it neither launches a
# second instance after the idle threshold nor resets our submap.
echo $$ >"$MARKER"

python3 "$HERE/star-gaze.py" --seconds "$SECS" &
pid=$!

cleanup() {
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  [ "$(cat "$MARKER" 2>/dev/null)" = "$$" ] && rm -f "$MARKER"
  hyprctl dispatch 'hl.dsp.submap("reset")' >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Arm keypress dismissal while the saver is up (the same submap the idle
# supervisor uses). dismiss.sh kills the runner and resets the submap; the
# cleanup trap is a safety net for workspace/lock/timeout exits.
sleep 0.4
hyprctl dispatch 'hl.dsp.submap("star-gaze")' >/dev/null 2>&1 || true

wait "$pid"
