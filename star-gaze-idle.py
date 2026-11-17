#!/usr/bin/env python3
"""star-gaze idle supervisor — launches the saver on shell idle, keeps it up.

Uses the Omarchy shell's own idle state (`omarchy-shell idle status`), so the
launch moment always matches the stock screensaver timing (it follows
~/.config/omarchy/shell.json automatically) with zero new dependencies.

Keep-alive policy (parity with stock's "cycle remains armed"):
  - the shell `idle` flag is consulted ONLY to launch. Once the saver is up,
    post-launch idle-flips are ignored entirely: starting a fullscreen saver
    makes the compositor report spurious activity, and that flip latches the
    flag, so it can never be used for dismissal.
  - keypress dismissal is handled by Hyprland itself: while the saver runs we
    sit in the `star-gaze` submap (~/.config/hypr/bindings.lua), whose
    catchall runs dismiss.sh (pkill the runner + leave the submap).
  - mouse movement alone never dismisses (hover steering in the saver).
  - workspace/focus change exits the runner via its focus watcher, lock
    screen exits it via its lock watcher, signals terminate it.
  - own lock timer: the stock shell cancels its lock when its (disabled)
    saver never appears, so we drive `omarchy system lock` ourselves from
    its lockDelay.
"""
import json
import os
import subprocess
import sys
import time

HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
RUNNER = __import__("os").path.join(HERE, "star-gaze.py")
LOG_PATH = "/tmp/star-gaze-idle.log"
POLL = 1.0


def log(msg):
    line = f"[star-gaze-idle] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


idle_query_ok = True


def shell_idle_status():
    """Shell idle state, or None while the shell is still starting up."""
    global idle_query_ok
    try:
        out = subprocess.check_output(["omarchy-shell", "idle", "status"], timeout=5)
    except Exception as e:
        if idle_query_ok:
            log(f"idle status query failed (shell not ready yet?): {e}")
            idle_query_ok = False
        return None
    if not idle_query_ok:
        log("idle status query recovered")
        idle_query_ok = True
    try:
        return json.loads(out.decode())
    except Exception as e:
        log(f"idle status decode failed: {e}")
        return None


def shell_is_locked():
    try:
        out = subprocess.check_output(["omarchy-shell", "lock", "isLocked"], timeout=5)
        return out.decode().strip() == "true"
    except Exception:
        return False


def submap_enter():
    subprocess.run(["hyprctl", "dispatch", 'hl.dsp.submap("star-gaze")'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def submap_reset():
    subprocess.run(["hyprctl", "dispatch", 'hl.dsp.submap("reset")'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def restore_cursor():
    """Safety net: unhide the pointer if a saver died without restoring it."""
    subprocess.run(
        ["hyprctl", "eval", "hl.config({cursor = {invisible = false}})"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def clear_strays():
    # "[s]" bracket trick: never matches our own pkill command line.
    subprocess.run(["pkill", "-f", "[s]tar-gaze[.]py"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_proc(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    restore_cursor()
    submap_reset()


PIDFILE = "/tmp/star-gaze-idle.pid"
PREVIEWFILE = "/tmp/star-gaze-preview.pid"


def manual_preview_active():
    """True while preview.sh owns a saver (menu-launched, no idle cycle)."""
    try:
        with open(PREVIEWFILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def main():
    # Single instance via pidfile (cmdline text matching can't tell our
    # launcher wrappers apart from a real supervisor).
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
    submap_reset()  # recover from a previous crash while saver was up
    proc = None
    launch_time = time.monotonic()
    lock_fired = False
    if test_launch:
        log("test-launch: starting runner immediately (supervision stays on)")
        proc = subprocess.Popen([sys.executable, RUNNER])
        launch_time = time.monotonic()
        submap_enter()

    log("supervisor started")
    while True:
        time.sleep(POLL)
        now = time.monotonic()

        st = shell_idle_status()
        if st is None:
            continue
        locked = shell_is_locked()
        want = bool(st.get("enabled")) and not bool(st.get("stayAwake"))
        idle = bool(st.get("idle"))
        manual = manual_preview_active()
        running = proc is not None and proc.poll() is None
        if proc is not None and proc.poll() is not None:
            # Dismissed: keypress (submap catchall), workspace switch
            # (focus watcher), lock (lock watcher), or a crash.
            log(f"saver exited (rc={proc.returncode})")
            proc = None
            running = False
            restore_cursor()
            if not manual:
                submap_reset()

        if not running and want and idle and not locked and not manual:
            log("idle threshold reached -> launching saver")
            proc = subprocess.Popen([sys.executable, RUNNER])
            launch_time = now
            lock_fired = False
            submap_enter()
        elif running:
            if not want or locked:
                reason = ("supervision off" if not want else "locked")
                log(f"killing saver: {reason}")
                stop_proc(proc)
                proc = None
                continue
            try:
                lock_delay = float(st.get("lockDelay", 150) or 150)
            except (TypeError, ValueError):
                lock_delay = 150.0
            age = now - launch_time
            if (not lock_fired and lock_delay > 0 and age >= lock_delay
                    and want and not locked):
                log(f"lock deadline reached ({age:.0f}s >= {lock_delay:.0f}s) "
                    f"-> stopping saver, locking system")
                lock_fired = True
                stop_proc(proc)
                proc = None
                subprocess.run(["omarchy", "system", "lock"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                continue
            # Otherwise: keep running. The shell idle flag is NOT consulted
            # while up (launch-time flip latches it); dismissal arrives as
            # runner exit via catchall key / workspace / lock watchers.


if __name__ == "__main__":
    main()
