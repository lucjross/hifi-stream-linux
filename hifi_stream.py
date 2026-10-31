#!/usr/bin/env python3
"""hifi-stream: play desktop audio on a network player as a live lossless FLAC stream.

Creates a virtual PipeWire output. Whatever plays to it is captured, encoded to FLAC and
served over HTTP, and the player picked in the tray menu is told to play that URL. Kinds
of player are backends; WiiM (LinkPlay) is built in, and more can be added as plugins.

  hifi-stream app                tray app: virtual output, stream server, auto-start (default)
  hifi-stream serve              virtual output and stream server only, no tray
  hifi-stream play ADDRESS...    point players at the stream once

Settings live in ~/.config/hifi-stream/config.json (written with defaults on first run),
backend plugins in ~/.config/hifi-stream/backends/*.py.
"""
import argparse
import json
import os
import re
import runpy
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

APP = "hifi-stream"
UNIT = f"{APP}.service"
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP / "config.json"
STATE_PATH = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / APP / "state.json"
PLUGIN_DIR = CONFIG_PATH.parent / "backends"
AUTOSTART_PATH = CONFIG_PATH.parent.parent / "autostart" / f"{APP}.desktop"

DEFAULTS = {
    "port": 8765,
    "sink_name": "hifi_stream",
    "sink_description": "",        # empty = "Hi-fi stream (24-bit FLAC)"
    "sample_rate": 44100,          # Spotify's native rate; anything else means resampling
    "bit_depth": 24,               # 16 or 24
    "idle_release_minutes": 5,     # stop the player after this long without audio; 0 = never
    "devices": {},                 # players discovery misses: {"name": "ip"} for a WiiM, or
                                   # {"name": {"backend": "...", "address": "..."}}
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
    cfg["sink_description"] = cfg["sink_description"] or f"Hi-fi stream ({cfg['bit_depth']}-bit FLAC)"
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
        "hifi_stream.managed": True,       # lets the uninstaller find it
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


# --- backends ---------------------------------------------------------------------------

BACKENDS = {}   # name -> Backend instance


def register(cls):
    """Class decorator that makes a Backend available."""
    BACKENDS[cls.name] = cls()
    return cls


class Backend:
    """A kind of network player that can be told to play an HTTP URL.

    To support another kind (UPnP/DLNA, Cast, ...), subclass this and decorate it with
    @register, either in this file or in a plugin file in ~/.config/hifi-stream/backends/,
    where Backend and register are predefined. Addresses are whatever the backend needs to
    reach a player, as written in the "devices" setting or returned by discover().
    """
    name = ""       # id used in settings and state, e.g. "wiim"
    label = ""      # shown in the menu when several backends are loaded, e.g. "WiiM"

    def discover(self):
        """Return {display name: address} of players found on the network."""
        return {}

    def play(self, address, url):
        raise NotImplementedError

    def stop(self, address):
        raise NotImplementedError

    def host(self, address):
        """The IP the player fetches the stream from, used to tell when it's connected."""
        return socket.gethostbyname(address)


def load_plugins():
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        try:
            runpy.run_path(str(path), init_globals={"Backend": Backend, "register": register})
        except Exception as e:
            print(f"{path}: {e}", file=sys.stderr)


def stream_url(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((host, 80))  # no packets sent; just picks the interface that reaches host
        return f"http://{s.getsockname()[0]}:{port}/stream.flac"


# --- WiiM -------------------------------------------------------------------------------

def wiim_cmd(host, command, timeout=5):
    ctx = ssl._create_unverified_context()  # WiiMs use a self-signed cert
    url = f"https://{host}/httpapi.asp?command={urllib.parse.quote(command, safe=':/')}"
    with urllib.request.urlopen(url, context=ctx, timeout=timeout) as r:
        return r.read().decode()


@register
class WiiM(Backend):
    """WiiM and other LinkPlay-based players, via their HTTP API. To play on several,
    group them in the WiiM Home app and pick the group leader."""
    name = "wiim"
    label = "WiiM"

    def discover(self):
        """mDNS, plus each player's group followers, which don't always answer mDNS."""
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

    def play(self, address, url):
        wiim_cmd(address, f"setPlayerCmd:play:{url}")

    def stop(self, address):
        wiim_cmd(address, "setPlayerCmd:stop")


def parse_device(entry):
    """A device entry from settings or state: "address" (a WiiM), or
    {"backend": "...", "address": "..."}. Returns (backend name, address)."""
    if isinstance(entry, str):
        return "wiim", entry
    return entry.get("backend", "wiim"), entry["address"]


# --- controller -------------------------------------------------------------------------

class Controller:
    """Starts the selected player when audio starts playing into the sink, and releases it
    after a long silence. It only takes the player over when playback starts, so switching
    the player to another source isn't undone while the desktop keeps playing."""

    def __init__(self, cfg):
        self.cfg = cfg
        state = read_json(STATE_PATH)
        self.discovered = {n: parse_device(e) for n, e in state.get("discovered", {}).items()}
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
        """{name: (backend name, address)} for every player with a loaded backend."""
        configured = {n: parse_device(e) for n, e in self.cfg["devices"].items()}
        return {n: d for n, d in {**self.discovered, **configured}.items() if d[0] in BACKENDS}

    def _device(self, name):
        d = self.devices.get(name)
        return d and (BACKENDS[d[0]], d[1])

    def _save(self):
        discovered = {n: {"backend": b, "address": a} for n, (b, a) in self.discovered.items()}
        write_json(STATE_PATH, {"selected": self.selected, "discovered": discovered})

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

    def _streaming_to(self, dev):
        backend, address = dev
        try:
            ip = backend.host(address)
        except OSError:
            return False
        with self.lock:
            return ip in self.clients

    def _call(self, what, fn):
        try:
            fn()
        except Exception as e:
            print(f"{what}: {e}", file=sys.stderr)

    def _start(self, dev):
        backend, address = dev
        self._call(f"{backend.name} {address} play",
                   lambda: backend.play(address, stream_url(backend.host(address), self.cfg["port"])))

    def _stop(self, dev):
        backend, address = dev
        self._call(f"{backend.name} {address} stop", lambda: backend.stop(address))

    def _scan(self):
        for backend in BACKENDS.values():
            try:
                self.discovered.update({n: (backend.name, a) for n, a in backend.discover().items()})
            except Exception as e:
                print(f"{backend.name} discovery: {e}", file=sys.stderr)
        self._save()
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
        dev = self._device(self.selected)
        idle_limit = self.cfg["idle_release_minutes"] * 60
        if pending:
            old = self._device(pending[0])
            was_streaming = bool(old) and self._streaming_to(old)
            if was_streaming and old != dev:
                self._stop(old)
            if dev and (active or was_streaming) and not self._streaming_to(dev):
                self._start(dev)
        elif started and dev and not self._streaming_to(dev):
            self._start(dev)
        elif not active and dev and idle_limit and self._streaming_to(dev) and now - idle_since > idle_limit:
            self._stop(dev)

        fmt = f"{self.cfg['bit_depth']}-bit/{self.cfg['sample_rate'] / 1000:g}kHz"
        if not self.selected:
            self.status = "Off"
        elif dev and self._streaming_to(dev):
            self.status = f"Streaming {fmt}" if active else "Connected, nothing playing"
        elif active:
            self.status = f"Not playing on {self.selected}"
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


def autostart_state():
    """"enabled" or "disabled" for however install.sh set up starting at login (a systemd
    user service, or an XDG autostart entry where there's no systemd graphical session),
    or None when it didn't."""
    state = systemctl("is-enabled").stdout.strip()
    if state in ("enabled", "disabled"):
        return state
    if AUTOSTART_PATH.exists():
        return "disabled" if "Hidden=true" in AUTOSTART_PATH.read_text().splitlines() else "enabled"
    return None


def set_autostart(on):
    if systemctl("is-enabled").stdout.strip() in ("enabled", "disabled"):
        systemctl("enable" if on else "disable")
    elif AUTOSTART_PATH.exists():
        lines = [l for l in AUTOSTART_PATH.read_text().splitlines() if not l.startswith("Hidden=")]
        AUTOSTART_PATH.write_text("\n".join(lines + ([] if on else ["Hidden=true"])) + "\n")


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
    ind.set_title("Hi-fi stream")
    shown = {}

    def item(menu, label, action):
        it = Gtk.MenuItem(label=label)
        it.connect("activate", lambda _: action())
        menu.append(it)

    def on_pick(it, name):
        if it.get_active():
            ctl.select(name)

    def on_autostart(it):
        set_autostart(it.get_active())

    def restart():
        os.execv(sys.executable, [sys.executable, *sys.argv])

    def build():
        menu = Gtk.Menu()
        group = None
        devices = ctl.devices
        for name in [None, *sorted(devices)]:
            label = name or "Off"
            if name and len(BACKENDS) > 1:
                label += f" ({BACKENDS[devices[name][0]].label})"
            it = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
            group = it
            it.set_active(name == ctl.selected)
            it.connect("toggled", on_pick, name)
            menu.append(it)
        menu.append(Gtk.SeparatorMenuItem())
        shown["status"] = Gtk.MenuItem(label=ctl.status)
        shown["status"].set_sensitive(False)
        menu.append(shown["status"])
        item(menu, "Rescan for devices", ctl.rescan)
        menu.append(Gtk.SeparatorMenuItem())
        enabled = autostart_state()
        if enabled:  # only when installed
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
    p = sub.add_parser("play", help="point players at the stream once")
    p.add_argument("--backend", default="wiim", help="kind of player (default: wiim)")
    p.add_argument("addresses", nargs="+")
    args = ap.parse_args()
    cfg = load_config()
    load_plugins()

    if args.cmd == "play":
        if args.backend not in BACKENDS:
            sys.exit(f"unknown backend {args.backend!r}; loaded: {', '.join(BACKENDS)}")
        backend = BACKENDS[args.backend]
        for address in args.addresses:
            backend.play(address, stream_url(backend.host(address), cfg["port"]))
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
