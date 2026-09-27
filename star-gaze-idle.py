#!/usr/bin/env python3
"""star-gaze idle supervisor — launches the saver on shell idle, kills on activity.

Uses the Omarchy shell's own idle state (`omarchy-shell idle status`), so the
launch moment always matches the stock screensaver timing (it follows
~/.config/omarchy/shell.json automatically) with zero new dependencies.

Dismiss policy (parity with stock, except the mouse):
  - keypresses / any non-mouse activity -> kill (shell reports active AND the
    mouse hasn't moved recently)
  - mouse movement alone -> saver keeps running (hover steering)
  - workspace/focus change, lock screen, signals -> kill
  - no saver timeout (like stock); the 300s Quickshell lock takes over
"""
import json
import subprocess
import sys
import time

HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
RUNNER = __import__("os").path.join(HERE, "star-gaze.py")
LOG_PATH = "/tmp/star-gaze-idle.log"
POLL = 1.0
MOUSE_STILL_SECS = 3.0


def log(msg):
    line = f"[star-gaze-idle] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def shell_idle_status():
    try:
        out = subprocess.check_output(["omarchy-shell", "idle", "status"], timeout=5)
        return json.loads(out.decode())
    except Exception as e:
        log(f"idle status query failed: {e}")
        return None


def shell_is_locked():
    try:
        out = subprocess.check_output(["omarchy-shell", "lock", "isLocked"], timeout=5)
        return out.decode().strip() == "true"
    except Exception:
        return False


def cursor_pos():
    try:
        out = subprocess.check_output(["hyprctl", "cursorpos", "-j"], timeout=2)
        pos = json.loads(out.decode())
        return (pos["x"], pos["y"])
    except Exception:
        return None


def clear_strays():
    # "[s]" bracket trick: never matches our own pkill command line.
    subprocess.run(["pkill", "-f", "[s]tar-gaze[.]py"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


PIDFILE = "/tmp/star-gaze-idle.pid"


def main():
    # Single instance via pidfile (cmdline text matching can't tell our
    # launcher wrappers apart from a real supervisor).
    import os
    try:
        with open(PIDFILE) as f:
            other = int(f.read().strip())
        os.kill(other, 0)
        if other != os.getpid():
            log(f"another supervisor running (pid {other}) — exiting")
            return 0
    except (OSError, ValueError):
        pass  # stale or missing pidfile -> take over
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))

    test_launch = "--test-launch" in sys.argv
    clear_strays()
    proc = None
    if test_launch:
        log("test-launch: starting runner immediately (supervision stays on)")
        proc = subprocess.Popen([sys.executable, RUNNER])

    last_mouse = cursor_pos()
    last_mouse_move = 0.0  # unknown at start -> treat as still (safe: allows launch)
    mouse_known_still = True
    log("supervisor started")
    while True:
        time.sleep(POLL)
        now = time.monotonic()

        cur = cursor_pos()
        if cur is not None and last_mouse is not None and cur != last_mouse:
            last_mouse_move = now
            mouse_known_still = False
        elif cur is not None and last_mouse is None:
            last_mouse_move = now
            mouse_known_still = False
        last_mouse = cur
        mouse_still = (now - last_mouse_move) > MOUSE_STILL_SECS

        st = shell_idle_status()
        if st is None:
            continue
        locked = shell_is_locked()
        want = bool(st.get("enabled")) and not bool(st.get("stayAwake"))
        idle = bool(st.get("idle"))
        running = proc is not None and proc.poll() is None
        if running and proc.poll() is not None:
            log(f"runner exited on its own (rc={proc.returncode})")
            proc = None
            running = False

        if not running and want and idle and not locked:
            log("idle threshold reached -> launching saver")
            proc = subprocess.Popen([sys.executable, RUNNER])
        elif running and (not want or locked or (not idle and mouse_still)):
            reason = ("supervision off" if not want else
                      "locked" if locked else "activity (mouse still)")
            log(f"killing saver: {reason}")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            proc = None


if __name__ == "__main__":
    main()
