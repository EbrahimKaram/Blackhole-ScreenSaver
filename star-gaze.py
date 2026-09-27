#!/usr/bin/env python3
"""star-gaze screensaver runner — raytraced black hole, fullscreen per monitor.

Host: GStreamer (appsrc time-code -> glupload -> glshader -> waylandsink).
No new dependencies beyond what Omarchy ships (python3-gobject,
gst-plugins-base, waylandsink).

Time: the glshader `time` uniform is unix-epoch based (unusable for smooth
animation), so we feed our own clock: appsrc pushes solid frames whose first
two rows encode t*256 as RGB24 (R + G*256 + B*65536 tics). The shader only
samples that patch; the scene itself is fully procedural.

Dismiss: any WM-level escape (workspace switch), --seconds timeout,
SIGINT/SIGTERM, or a keypress if stdin is a live tty.
"""
import argparse
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FRAG_PATH = os.path.join(HERE, "star-gaze.frag")
LOG_PATH = "/tmp/star-gaze.log"

# Internal render resolution (16:9; the sink upscales to the monitor).
RENDER_W, RENDER_H, RENDER_FPS = 1280, 720, 60

stop = threading.Event()
t0 = time.monotonic()

# Mouse steering state (smoothed on CPU; shader just adds the offsets).
# Rows 1..3 of each frame carry yaw / pitch / influence as full-row colors.
YAW_RANGE, PITCH_RANGE = 0.7, 0.32
mouse = {"yaw": 0.0, "pitch": 0.0, "inf": 0.0, "lock": threading.Lock()}


def log(msg):
    line = f"[star-gaze] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def hypr(*args):
    try:
        out = subprocess.check_output(["hyprctl", *args], timeout=5)
        return json.loads(out.decode())
    except Exception as e:
        log(f"hyprctl {' '.join(args)} failed: {e}")
        return None


def set_cursor(invisible):
    val = "true" if invisible else "false"
    subprocess.run(
        ["hyprctl", "keyword", "cursor:invisible", val],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def build_pipeline(monitor, frag_src, fps_report):
    name = monitor["name"]
    els = {}
    els["appsrc"] = Gst.ElementFactory.make("appsrc", f"timesrc-{name}")
    for kind in ["glupload", "glshader", "gldownload", "videoconvert",
                 "fpsdisplaysink", "waylandsink"]:
        el = Gst.ElementFactory.make(kind, None)
        if el is None:
            raise RuntimeError(f"missing GStreamer element: {kind}")
        els[kind] = el
    if els["appsrc"] is None:
        raise RuntimeError("missing GStreamer element: appsrc")

    src = els["appsrc"]
    src.set_property("caps", Gst.Caps.from_string(
        f"video/x-raw,format=RGBA,width={RENDER_W},height={RENDER_H},"
        f"framerate={RENDER_FPS}/1"))
    src.set_property("is-live", True)
    src.set_property("format", Gst.Format.TIME)
    src.set_property("do-timestamp", True)
    src.set_property("block", True)
    src.set_property("max-buffers", 8)
    src.set_property("emit-signals", True)

    frame_size = RENDER_W * RENDER_H * 4
    row_len = RENDER_W * 4

    def on_need_data(appsrc, _length):
        if stop.is_set():
            return
        t = time.monotonic() - t0
        tic = int(t * 256.0) & 0xFFFFFF
        time_px = bytes((tic & 255, (tic >> 8) & 255, (tic >> 16) & 255, 255))
        if globals().get("test_steer"):
            yaw, pitch, inf = globals()["test_steer"]
        else:
            with mouse["lock"]:
                yaw, pitch, inf = mouse["yaw"], mouse["pitch"], mouse["inf"]
        yaw_px = bytes((int((yaw / YAW_RANGE * 0.5 + 0.5) * 255), 0, 0, 255))
        pitch_px = bytes((int((pitch / PITCH_RANGE * 0.5 + 0.5) * 255), 0, 0, 255))
        inf_px = bytes((int(max(0.0, min(1.0, inf)) * 255), 0, 0, 255))
        buf = Gst.Buffer.new_allocate(None, frame_size, None)
        # rows 0..3: time, yaw, pitch, influence (full-row uniform colors)
        buf.fill(0, time_px * RENDER_W + yaw_px * RENDER_W +
                 pitch_px * RENDER_W + inf_px * RENDER_W)
        flow = appsrc.emit("push-buffer", buf)
        if flow != Gst.FlowReturn.OK:
            log(f"push-buffer on {name}: {flow}")

    src.connect("need-data", on_need_data)

    els["glshader"].set_property("fragment", frag_src)
    els["fpsdisplaysink"].set_property("text-overlay", False)
    els["fpsdisplaysink"].set_property("signal-fps-measurements", True)
    els["fpsdisplaysink"].connect(
        "fps-measurements",
        lambda _e, fps, _drop, _avg: fps_report(name, fps),
    )
    els["waylandsink"].set_property("fullscreen", True)
    els["waylandsink"].set_property("fullscreen-output", name)
    els["fpsdisplaysink"].set_property("video-sink", els["waylandsink"])

    pipe = Gst.Pipeline.new(f"star-gaze-{name}")
    chain = ["appsrc", "glupload", "glshader",
             "gldownload", "videoconvert", "fpsdisplaysink"]
    for kind in chain:
        pipe.add(els[kind])
    for a, b in zip(chain, chain[1:]):
        if not els[a].link(els[b]):
            raise RuntimeError(f"link failed: {a} -> {b} on {name}")
    return pipe


def stdin_watcher():
    if not sys.stdin.isatty():
        return
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        try:
            while not stop.is_set():
                r, _, _ = select.select([sys.stdin], [], [], 0.5)
                if r and sys.stdin.read(1):
                    log("keypress on stdin — exiting")
                    stop.set()
                    return
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception as e:
        log(f"stdin watcher off: {e}")


def focus_watcher(grace=4.0):
    """Exit if the user escapes the saver at WM level (workspace switch)."""
    time.sleep(grace)
    start_ws = None
    while not stop.is_set():
        time.sleep(0.4)
        cur = hypr("activeworkspace", "-j")
        if not cur:
            continue
        wid = cur.get("id")
        if start_ws is None:
            start_ws = wid
            log(f"watchdog armed on workspace {wid}")
        elif wid != start_ws:
            log(f"workspace changed {start_ws} -> {wid} — exiting")
            stop.set()
            return


def mouse_thread(monitors):
    """Hover steering: eased yaw/pitch offsets, fading back to cinematic
    a few seconds after the mouse stops. Never dismisses on mouse alone."""
    mx, my = None, None
    active_until = 0.0
    last = time.monotonic()
    while not stop.is_set():
        time.sleep(0.05)
        now = time.monotonic()
        dt = min(now - last, 0.25)
        last = now
        try:
            out = subprocess.check_output(["hyprctl", "cursorpos", "-j"], timeout=2)
            pos = json.loads(out.decode())
            cx, cy = pos["x"], pos["y"]
        except Exception:
            continue
        mon = next((m for m in monitors
                    if m["x"] <= cx < m["x"] + m["width"] and
                    m["y"] <= cy < m["y"] + m["height"]), monitors[0])
        nx = ((cx - mon["x"]) / mon["width"]) * 2.0 - 1.0
        ny = ((cy - mon["y"]) / mon["height"]) * 2.0 - 1.0
        if mx is not None and abs(cx - mx) + abs(cy - my) > 6:
            active_until = now + 4.0
        mx, my = cx, cy
        k = 1.0 - pow(2.71828, -dt * 4.0)
        ki = 1.0 - pow(2.71828, -dt * 2.0)
        with mouse["lock"]:
            mouse["yaw"] += (-nx * YAW_RANGE - mouse["yaw"]) * k
            mouse["pitch"] += (-ny * PITCH_RANGE - mouse["pitch"]) * k
            goal = 1.0 if now < active_until else 0.0
            mouse["inf"] += (goal - mouse["inf"]) * ki


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=0,
                    help="auto-exit after N seconds (0 = run until dismissed)")
    ap.add_argument("--fps-log-interval", type=float, default=2.0)
    ap.add_argument("--steer", default="",
                    help="test override 'yaw,pitch,inf' (bypasses mouse poll)")
    args = ap.parse_args()

    test_steer = None
    if args.steer:
        test_steer = [float(v) for v in args.steer.split(",")]
        log(f"test steer override: {test_steer}")
    globals()["test_steer"] = test_steer

    Gst.init(None)
    with open(FRAG_PATH) as f:
        frag_src = f.read()

    monitors = hypr("monitors", "-j")
    if not monitors:
        log("no monitors found — exiting")
        return 1
    log(f"monitors: {[(m['name'], m['width'], m['height']) for m in monitors]}")

    last_fps_log = {}

    def fps_report(name, fps):
        now = time.monotonic()
        if now - last_fps_log.get(name, 0) >= args.fps_log_interval:
            last_fps_log[name] = now
            log(f"fps@{name}: {fps:.1f}")

    pipes = []
    try:
        for m in monitors:
            pipe = build_pipeline(m, frag_src, fps_report)
            ret = pipe.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError(f"PLAYING failed on {m['name']}")
            pipes.append(pipe)
            log(f"playing on {m['name']}")
    except Exception as e:
        log(f"startup failed: {e}")
        for pipe in pipes:
            pipe.set_state(Gst.State.NULL)
        return 1

    global t0
    t0 = time.monotonic()  # scene clock starts when pixels flow
    set_cursor(True)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    threading.Thread(target=stdin_watcher, daemon=True).start()
    threading.Thread(target=focus_watcher, daemon=True).start()
    threading.Thread(target=mouse_thread, args=(monitors,), daemon=True).start()

    deadline = time.monotonic() + args.seconds if args.seconds > 0 else None
    while not stop.is_set():
        if deadline and time.monotonic() >= deadline:
            log("timeout reached — exiting")
            break
        for pipe in pipes:
            bus = pipe.get_bus()
            while True:
                msg = bus.pop()
                if msg is None:
                    break
                if msg.type == Gst.MessageType.ERROR:
                    err, dbg = msg.parse_error()
                    log(f"GStreamer ERROR: {err} ({dbg})")
                    stop.set()
        time.sleep(0.2)

    for pipe in pipes:
        pipe.set_state(Gst.State.NULL)
    set_cursor(False)
    log("stopped, cursor restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
