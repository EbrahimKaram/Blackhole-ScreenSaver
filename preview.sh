#!/bin/bash
# star-gaze preview — runs the shader saver without touching idle settings.
# Usage: preview.sh [seconds]   (default 20)
# Dismiss early: switch workspace (Super+1..) or Ctrl+C here.
set -u
SECS="${1:-20}"
exec python3 "$(dirname "$(readlink -f "$0")")/star-gaze.py" --seconds "$SECS"
