#!/bin/bash
# star-gaze dismiss — runs on any keypress while the star-gaze submap is
# active (the saver is up). Kills the saver runner, then leaves the submap.
# No-op when the saver isn't running, so a stuck submap self-heals on the
# next keypress.
pkill -f '[s]tar-gaze[.]py' || true
hyprctl dispatch 'hl.dsp.submap("reset")' >/dev/null 2>&1 || true
