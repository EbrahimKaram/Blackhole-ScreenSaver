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
  appsrc time-code feed, mouse steering, keypress helper, lock watch
- `star-gaze-keys.sh` — hidden 1px input helper (any-key dismiss)
- `preview.sh` — manual preview: `preview.sh [seconds]` (default 20)

## Idle wiring (Omarchy)

- `hypridle` launches the runner after 150s idle (launch-only listener;
  Quickshell keeps owning screen lock at 300s)
- stock saver disabled via `omarchy toggle screensaver` to avoid double launch
- `hypridle` autostarted from `~/.config/hypr/autostart.lua`

## Push to GitHub

```bash
cd ~/.config/omarchy/screensaver
gh repo create star-gaze --private --source=. --push
```
