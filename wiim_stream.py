#!/usr/bin/env python3
"""wiim-stream: play Linux desktop audio on a WiiM as a live lossless FLAC stream.

Creates a virtual PipeWire output. Whatever plays to it (e.g. the Spotify desktop app,
Mix transitions included) is captured, encoded to 24-bit FLAC and streamed over HTTP to
the WiiM picked in the tray menu. To play on several WiiMs, group them in the WiiM Home
app and pick the group leader.

  wiim-stream app          tray app: virtual output, stream server, auto-start (default)
  wiim-stream serve        virtual output and stream server only, no tray
  wiim-stream play IP...   point WiiMs at the stream once

Settings live in ~/.config/wiim-stream/config.json (written with defaults on first run).
"""
import argparse
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP = "wiim-stream"
UNIT = f"{APP}.service"
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP / "config.json"
STATE_PATH = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / APP / "state.json"

DEFAULTS = {
    "port": 8765,
    "sink_name": "wiim_stream",
    "sink_description": "",        # empty = "WiiM (24-bit FLAC stream)"
    "sample_rate": 44100,          # Spotify's native rate; anything else means resampling
    "bit_depth": 24,               # 16 or 24
    "idle_release_minutes": 5,     # stop the WiiM after this long without audio; 0 = never
    "devices": {},                 # extra WiiMs as {"name": "ip"}, if discovery misses them
}

SCAN_SECS = 60
POLL_SECS = 1.5


# --- config and state -------------------------------------------------------------------

def load_config():
    if not CONFIG_PATH.exists():
        write_json(CONFIG_PATH, DEFAULTS)
    cfg = {**DEFAULTS, **read_json(CONFIG_PATH)}
    if cfg["bit_depth"] not in (16, 24):
        sys.exit(f"{CONFIG_PATH}: bit_depth must be 16 or 24")
    cfg["sink_description"] = cfg["sink_description"] or f"WiiM ({cfg['bit_depth']}-bit FLAC stream)"
    return cfg


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


# --- PipeWire ---------------------------------------------------------------------------

def pw_dump():
    return json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, check=True).stdout)


def find_node(objs, name):
    return next((o for o in objs if o["type"].endswith("Node")
                 and o["info"]["props"].get("node.name") == name), None)


def ensure_sink(cfg):
    """Create the virtual output, or recreate it if its settings changed."""
    props = {
        "factory.name": "support.null-audio-sink",
        "node.name": cfg["sink_name"],
        "node.description": cfg["sink_description"],
        "media.class": "Audio/Sink",
        "audio.position": ["FL", "FR"],
        "audio.rate": cfg["sample_rate"],
        "monitor.channel-volumes": True,   # the desktop volume slider applies to the stream
        "object.linger": True,             # outlive pw-cli, and this app across restarts
        "wiim_stream.managed": True,       # lets the uninstaller find it
    }
    node = find_node(pw_dump(), cfg["sink_name"])
    if node:
        p = node["info"]["props"]
        if p.get("node.description") == props["node.description"] and p.get("audio.rate") == props["audio.rate"]:
            return
        subprocess.run(["pw-cli", "destroy", str(node["id"])], capture_output=True)
    subprocess.run(["pw-cli", "create-node", "adapter", json.dumps(props)], capture_output=True, check=True)


def allow_rate(rate):
    """Add rate to PipeWire's allowed clock rates so the graph runs at it, unresampled."""
    out = subprocess.run(["pw-metadata", "-n", "settings"], capture_output=True, text=True).stdout
    m = re.search(r"key:'clock\.allowed-rates' value:'\[([^\]]*)\]'", out)
    rates = {int(r) for r in re.findall(r"\d+", m[1])} if m else {48000}
    if rate not in rates:
        rates.add(rate)
        value = "[ " + ", ".join(map(str, sorted(rates))) + " ]"
        subprocess.run(["pw-metadata", "-n", "settings", "0", "clock.allowed-rates", value],
                       capture_output=True, check=True)


def sink_has_audio(name):
    """True when some app is actively playing into the sink (not paused or idle)."""
    objs = pw_dump()
    sink = find_node(objs, name)
    return bool(sink) and any(o["type"].endswith("Link") and o["info"].get("input-node-id") == sink["id"]
                              and o["info"].get("state") == "active" for o in objs)


# --- stream server ----------------------------------------------------------------------

def capture_pipeline(cfg):
    # Capture the sink's monitor straight into ffmpeg (pw-record buffers ~3s when piped).
    # Small fragments keep latency low; 32-bit capture keeps all 24 bits for the encoder.
    depth = (["-sample_fmt", "s32", "-bits_per_raw_sample", "24"] if cfg["bit_depth"] == 24
             else ["-sample_fmt", "s16"])
    return subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "pulse", "-c:a", "pcm_s32le", "-sample_rate", str(cfg["sample_rate"]), "-channels", "2",
         "-fragment_size", "8192", "-stream_name", f"{APP} capture",
         "-i", f"{cfg['sink_name']}.monitor",
         "-c:a", "flac", *depth, "-compression_level", "0", "-flush_packets", "1", "-f", "flac", "pipe:1"],
        stdout=subprocess.PIPE,
    )


class Handler(BaseHTTPRequestHandler):
    def _headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/flac")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_HEAD(self):
        self._headers()

    def do_GET(self):
        if not self.path.startswith("/stream.flac"):
            self.send_error(404)
            return
        self._headers()
        ctl = getattr(self.server, "ctl", None)
        ip = self.client_address[0]
        if ctl:
            ctl.client_changed(ip, +1)
        enc = capture_pipeline(self.server.cfg)
        try:
            while chunk := enc.stdout.read1(4096):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            enc.kill()
            enc.wait()
            if ctl:
                ctl.client_changed(ip, -1)

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} {fmt % args}", file=sys.stderr)


# --- WiiM -------------------------------------------------------------------------------

def wiim_cmd(host, command, timeout=5):
    ctx = ssl._create_unverified_context()  # WiiMs use a self-signed cert
    url = f"https://{host}/httpapi.asp?command={urllib.parse.quote(command, safe=':/')}"
    with urllib.request.urlopen(url, context=ctx, timeout=timeout) as r:
        return r.read().decode()


def stream_url(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((host, 80))  # no packets sent; just picks the interface that reaches host
        return f"http://{s.getsockname()[0]}:{port}/stream.flac"


def discover():
    """Return {name: ip} of WiiMs (and other LinkPlay devices) on the network, via mDNS
    plus each one's group followers, which don't always answer mDNS."""
    found = {}
    out = subprocess.run(["avahi-browse", "-rtp", "_linkplay._tcp"],
                         capture_output=True, text=True, timeout=15).stdout
    for line in out.splitlines():
        f = line.split(";")
        if len(f) > 7 and f[0] == "=" and f[2] == "IPv4":
            name = re.sub(r"\\(\d{3})", lambda m: chr(int(m[1])), f[3])
            found[re.sub(r"\\(.)", r"\1", name)] = f[7]
    for ip in list(found.values()):
        try:
            for s in json.loads(wiim_cmd(ip, "multiroom:getSlaveList", 3)).get("slave_list", []):
                found.setdefault(s["name"], s["ip"])
        except Exception:
            pass
    return found


# --- controller -------------------------------------------------------------------------

class Controller:
    """Starts the selected WiiM when audio starts playing into the sink, and releases it
    after a long silence. It only takes the WiiM over when playback starts, so switching
    the WiiM to another source isn't undone while the desktop keeps playing."""

    def __init__(self, cfg):
        self.cfg = cfg
        state = read_json(STATE_PATH)
        self.discovered = state.get("discovered", {})
        self.selected = state.get("selected")
        self.clients = {}            # ip -> open stream connections
        self.active = False
        self.status = "Starting…"
        self.pending = None          # previous selection, when it just changed
        self.last_scan = 0
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.listeners = []

    @property
    def devices(self):
        return {**self.discovered, **self.cfg["devices"]}

    def _save(self):
        write_json(STATE_PATH, {"selected": self.selected, "discovered": self.discovered})

    def client_changed(self, ip, delta):
        with self.lock:
            self.clients[ip] = self.clients.get(ip, 0) + delta
            if self.clients[ip] <= 0:
                del self.clients[ip]
        self.wake.set()

    def select(self, name):
        with self.lock:
            if name == self.selected:
                return
            if self.pending is None:
                self.pending = (self.selected,)
            self.selected = name
            self._save()
        self.wake.set()

    def rescan(self):
        self.last_scan = 0
        self.wake.set()

    def _streaming_to(self, ip):
        with self.lock:
            return ip in self.clients

    def _send(self, ip, command):
        try:
            wiim_cmd(ip, command)
        except Exception as e:
            print(f"{ip} {command}: {e}", file=sys.stderr)

    def _start(self, ip):
        self._send(ip, f"setPlayerCmd:play:{stream_url(ip, self.cfg['port'])}")

    def _stop(self, ip):
        self._send(ip, "setPlayerCmd:stop")

    def _scan(self):
        try:
            self.discovered.update(discover())
            self._save()
        except Exception as e:
            print(f"discovery: {e}", file=sys.stderr)
        self.last_scan = time.monotonic()

    def _tick(self, idle_since):
        now = time.monotonic()
        if now - self.last_scan > SCAN_SECS:
            self._scan()
        try:
            active = sink_has_audio(self.cfg["sink_name"])
        except Exception as e:
            print(f"pw-dump: {e}", file=sys.stderr)
            active = self.active
        started = active and not self.active
        self.active = active
        if active:
            idle_since = now

        with self.lock:
            pending, self.pending = self.pending, None
        ip = self.devices.get(self.selected)
        idle_limit = self.cfg["idle_release_minutes"] * 60
        if pending:
            old_ip = self.devices.get(pending[0])
            was_streaming = bool(old_ip) and self._streaming_to(old_ip)
            if was_streaming and old_ip != ip:
                self._stop(old_ip)
            if ip and (active or was_streaming) and not self._streaming_to(ip):
                self._start(ip)
        elif started and ip and not self._streaming_to(ip):
            self._start(ip)
        elif not active and ip and idle_limit and self._streaming_to(ip) and now - idle_since > idle_limit:
            self._stop(ip)

        fmt = f"{self.cfg['bit_depth']}-bit/{self.cfg['sample_rate'] / 1000:g}kHz"
        if not self.selected:
            self.status = "Off"
        elif ip and self._streaming_to(ip):
            self.status = f"Streaming {fmt}" if active else "Connected, nothing playing"
        elif active:
            self.status = "Not playing on the WiiM"
        else:
            self.status = "Waiting for audio"
        for cb in self.listeners:
            cb()
        return idle_since

    def run(self):
        idle_since = time.monotonic()
        while True:
            idle_since = self._tick(idle_since)
            self.wake.wait(POLL_SECS)
            self.wake.clear()


# --- tray -------------------------------------------------------------------------------

def systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args, UNIT], capture_output=True, text=True)


def run_tray(ctl):
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator, GLib, Gtk

    # Installed into the user icon theme by install.sh; icons/ beside the script when run
    # from a checkout.
    icon = f"{APP}-symbolic"
    repo_icons = Path(__file__).resolve().parent / "icons"
    if not Gtk.IconTheme.get_default().has_icon(icon) and not (repo_icons / f"{icon}.svg").exists():
        icon = "audio-speakers-symbolic"
    ind = AppIndicator.Indicator.new(APP, icon, AppIndicator.IndicatorCategory.APPLICATION_STATUS)
    if repo_icons.is_dir():
        ind.set_icon_theme_path(str(repo_icons))
    ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
    ind.set_title("WiiM stream")
    shown = {}

    def item(menu, label, action):
        it = Gtk.MenuItem(label=label)
        it.connect("activate", lambda _: action())
        menu.append(it)

    def on_pick(it, name):
        if it.get_active():
            ctl.select(name)

    def on_autostart(it):
        systemctl("enable" if it.get_active() else "disable")

    def restart():
        os.execv(sys.executable, [sys.executable, *sys.argv])

    def build():
        menu = Gtk.Menu()
        group = None
        for name in [None, *sorted(ctl.devices)]:
            it = Gtk.RadioMenuItem.new_with_label_from_widget(group, name or "Off")
            group = it
            it.set_active(name == ctl.selected)
            it.connect("toggled", on_pick, name)
            menu.append(it)
        menu.append(Gtk.SeparatorMenuItem())
        shown["status"] = Gtk.MenuItem(label=ctl.status)
        shown["status"].set_sensitive(False)
        menu.append(shown["status"])
        item(menu, "Rescan for WiiMs", ctl.rescan)
        menu.append(Gtk.SeparatorMenuItem())
        enabled = systemctl("is-enabled").stdout.strip()
        if enabled in ("enabled", "disabled"):  # only when installed as a service
            auto = Gtk.CheckMenuItem(label="Start at login")
            auto.set_active(enabled == "enabled")
            auto.connect("toggled", on_autostart)
            menu.append(auto)
        item(menu, "Edit settings…", lambda: subprocess.Popen(["xdg-open", str(CONFIG_PATH)]))
        item(menu, "Restart (apply settings)", restart)
        item(menu, "Quit", lambda: os._exit(0))
        menu.show_all()
        ind.set_menu(menu)

    def refresh():
        key = (tuple(sorted(ctl.devices)), ctl.selected)
        if shown.get("key") != key:
            shown["key"] = key
            build()
        shown["status"].set_label(ctl.status)
        return False

    ctl.listeners.append(lambda: GLib.idle_add(refresh))
    refresh()
    Gtk.main()


# --- main -------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(prog=APP, description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("app", help="tray app (default)")
    sub.add_parser("serve", help="virtual output and stream server, no tray")
    p = sub.add_parser("play", help="point WiiMs at the stream once")
    p.add_argument("hosts", nargs="+")
    args = ap.parse_args()
    cfg = load_config()

    if args.cmd == "play":
        for host in args.hosts:
            print(host, wiim_cmd(host, f"setPlayerCmd:play:{stream_url(host, cfg['port'])}"))
        return

    allow_rate(cfg["sample_rate"])
    ensure_sink(cfg)
    server = ThreadingHTTPServer(("0.0.0.0", cfg["port"]), Handler)
    server.cfg = cfg
    if args.cmd == "serve":
        server.serve_forever()
        return
    ctl = server.ctl = Controller(cfg)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=ctl.run, daemon=True).start()
    run_tray(ctl)


if __name__ == "__main__":
    sys.exit(main())
