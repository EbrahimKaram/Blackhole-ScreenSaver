# star-gaze — raytraced black-hole screensaver for Omarchy/Hyprland

A custom idle screensaver: a Gargantua-style black hole (geodesic raymarch,
blackbody accretion disk, doppler beaming, Keplerian turbulence) with a
cinematic auto-orbiting camera plus mouse hover steering. Keypresses,
workspace switches, and the system lock dismiss it, like the stock saver.

Technique follows ["Raytracing a Black Hole with
WebGPU"](https://threejsroadmap.com/blog/raytracing-a-black-hole-with-webgpu)
(Dan Greenheck) — several disk constants and the blackbody fit are ported
from that demo's tuned values.

## Files

- `star-gaze.frag` — the GLSL scene (GStreamer `glshader` host)
- `star-gaze.py` — runner: fullscreen per monitor, cursor hide/restore,
  appsrc time-code feed, mouse steering, workspace/lock/signal dismiss
- `star-gaze-idle.py` — idle supervisor: polls `omarchy-shell idle status`
  (same timing source as stock), launches the runner at the idle threshold
  and keeps it up until lock. The shell `idle` flag is launch-only: the
  launch itself makes the compositor report spurious activity that latches
  the flag, so dismissal is handled elsewhere (below). Own lock timer from
  the shell's `lockDelay`, since the stock shell cancels its lock when its
  (disabled) saver never appears.
- `dismiss.sh` — keypress dismissal: the supervisor sits in the `star-gaze`
  subkeymap (`~/.config/hypr/bindings.lua`) while the saver runs; its
  catchall runs this (kill runner + leave submap). No-op when idle, so a
  stuck submap self-heals on the next keypress.

## Dismiss matrix (stock parity, except the mouse)

| Input | Stock | star-gaze |
|---|---|---|
| Keypress | exits | exits (submap catchall → dismiss.sh; the key is eaten, like a lock screen) |
| Workspace/focus change | exits | exits |
| Mouse move | ignored | steers the camera |
| Lock at 300s | takes over | takes over (supervisor runs `omarchy system lock`) |
| Timeout | none | none (lock deadline above) |
- `preview.sh` — manual preview: `preview.sh [seconds]` (default 20)

## Idle wiring (Omarchy)

- supervisor autostarted from `~/.config/hypr/autostart.lua`
- stock saver disabled via `omarchy toggle screensaver` to avoid double launch
- no new packages, no sudo, nothing outside `~/.config`

## Push to GitHub

```bash
cd ~/.config/omarchy/screensaver
gh repo create star-gaze --private --source=. --push
```
