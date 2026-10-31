#!/usr/bin/env bash
# Install hifi-stream for the current user and start it (it then runs at every login).
#
#   ./install.sh                 install or update from this checkout
#   ./install.sh --uninstall     remove (keeps ~/.config/hifi-stream)
#
# Or without cloning, which fetches the latest main from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/lucjross/hifi-stream-linux/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/lucjross/hifi-stream-linux/main/install.sh | bash -s -- --uninstall
set -euo pipefail

TARBALL=${HIFI_STREAM_TARBALL:-https://github.com/lucjross/hifi-stream-linux/archive/refs/heads/main.tar.gz}
CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
STATE_HOME=${XDG_STATE_HOME:-$HOME/.local/state}
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
BIN="$HOME/.local/bin/hifi-stream"
ICON="$ICON_DIR/hifi-stream-symbolic.svg"
UNIT_DIR="$CONFIG_HOME/systemd/user"
UNIT="$UNIT_DIR/hifi-stream.service"
AUTOSTART="$CONFIG_HOME/autostart/hifi-stream.desktop"
PY=/usr/bin/python3   # the distro Python, which has the GTK bindings

# Destroy the virtual outputs carrying the marker property $1, or named $2
destroy_sinks() {
    pw-dump 2>/dev/null | "$PY" -c '
import json, sys
for o in json.load(sys.stdin):
    p = o.get("info", {}).get("props", {})
    if p.get(sys.argv[1]) or (p.get("node.name") == sys.argv[2]
                              and p.get("factory.name") == "support.null-audio-sink"):
        print(o["id"])' "$1" "${2:-}" | xargs -r -n1 pw-cli destroy >/dev/null || true
}

uninstall() {
    systemctl --user disable --now hifi-stream.service 2>/dev/null || true
    pkill -f "^$PY $BIN app" 2>/dev/null || true
    rm -f "$UNIT" "$AUTOSTART" "$BIN" "$ICON"
    systemctl --user daemon-reload 2>/dev/null || true
    destroy_sinks hifi_stream.managed   # now rather than at the next PipeWire restart
    echo "Uninstalled. Settings are left in $CONFIG_HOME/hifi-stream."
}

# This app used to be called wiim-stream: replace that install, keeping its settings.
# Sets MOVED_DEFAULT when the old output was the sound output, so the new one can be.
migrate_wiim_stream() {
    local old_unit="$UNIT_DIR/wiim-stream.service"
    [[ -e $old_unit || -e $HOME/.local/bin/wiim-stream ]] || return 0
    echo "Replacing the old wiim-stream install…"
    systemctl --user disable --now wiim-stream.service 2>/dev/null || true
    rm -f "$old_unit" "$HOME/.local/bin/wiim-stream" "$ICON_DIR/wiim-stream-symbolic.svg"
    if [[ -d $CONFIG_HOME/wiim-stream && ! -e $CONFIG_HOME/hifi-stream ]]; then
        mv "$CONFIG_HOME/wiim-stream" "$CONFIG_HOME/hifi-stream"
    fi
    if [[ -d $STATE_HOME/wiim-stream && ! -e $STATE_HOME/hifi-stream ]]; then
        mv "$STATE_HOME/wiim-stream" "$STATE_HOME/hifi-stream"
    fi
    # The old default output name was written into the settings file; let the new one apply
    "$PY" - "$CONFIG_HOME/hifi-stream/config.json" <<'EOF' 2>/dev/null || true
import json, sys
path = sys.argv[1]
cfg = json.load(open(path))
if cfg.get("sink_name") == "wiim_stream":
    del cfg["sink_name"]
    open(path, "w").write(json.dumps(cfg, indent=2) + "\n")
EOF
    if pw-metadata 0 default.configured.audio.sink 2>/dev/null | grep -q '"wiim_stream"'; then
        MOVED_DEFAULT=1
    fi
    destroy_sinks wiim_stream.managed wiim_stream   # early versions didn't set the marker
}

check_deps() {
    local missing=()
    need() { command -v "$1" >/dev/null || missing+=("$2"); }
    need curl curl
    need ffmpeg ffmpeg
    need avahi-browse avahi-utils
    need pw-cli pipewire-bin
    need pw-dump pipewire-bin
    need pw-metadata pipewire-bin
    pgrep -u "$USER" -x pipewire-pulse >/dev/null || missing+=(pipewire-pulse)
    "$PY" -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, AyatanaAppIndicator3' 2>/dev/null \
        || missing+=(python3-gi gir1.2-ayatanaappindicator3-0.1)
    if ((${#missing[@]})); then
        echo "Missing dependencies. Install them with:"
        echo "  sudo apt install $(printf '%s\n' "${missing[@]}" | sort -u | tr '\n' ' ')"
        exit 1
    fi
    if ! grep -qw pulse <<<"$(ffmpeg -hide_banner -demuxers 2>/dev/null)"; then
        echo "This ffmpeg was built without PulseAudio input, which hifi-stream needs." >&2
        exit 1
    fi
}

# Start at login with a systemd user service where the desktop runs a systemd graphical
# session (GNOME, KDE Plasma), otherwise with an XDG autostart entry, which every desktop
# honours. Either way, start it now.
setup_autostart() {
    if systemctl --user is-active --quiet graphical-session.target 2>/dev/null; then
        rm -f "$AUTOSTART"
        mkdir -p "$UNIT_DIR"
        cat > "$UNIT" <<EOF
[Unit]
Description=hifi-stream: play desktop audio on a network player as lossless FLAC
PartOf=graphical-session.target
After=graphical-session.target pipewire.service pipewire-pulse.service

[Service]
ExecStart=$PY $BIN app
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable hifi-stream.service
        systemctl --user restart hifi-stream.service
    else
        if [[ -e $UNIT ]]; then
            systemctl --user disable --now hifi-stream.service 2>/dev/null || true
            rm -f "$UNIT"
        fi
        mkdir -p "$(dirname "$AUTOSTART")"
        cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Hi-fi stream
Comment=Play desktop audio on a network player as lossless FLAC
Exec=$PY $BIN app
Icon=hifi-stream-symbolic
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
        pkill -f "^$PY $BIN app" 2>/dev/null || true
        setsid "$PY" "$BIN" app >/dev/null 2>&1 < /dev/null &
    fi
}

install_app() {
    check_deps
    MOVED_DEFAULT=
    migrate_wiim_stream
    # Use this checkout, or download one when the script was piped from curl
    local self=${BASH_SOURCE[0]:-} src
    if [[ -n $self && -f $(dirname "$self")/hifi_stream.py ]]; then
        src=$(cd "$(dirname "$self")" && pwd)
    else
        src=$(mktemp -d)
        trap "rm -rf '$src'" EXIT   # expanded now: src is local and gone by exit
        echo "Downloading hifi-stream…"
        curl -fsSL "$TARBALL" | tar -xz -C "$src" --strip-components=1
    fi
    install -Dm755 "$src/hifi_stream.py" "$BIN"
    install -Dm644 "$src/icons/hifi-stream-symbolic.svg" "$ICON"
    touch "${ICON%/scalable/*}"   # bump the theme dir's mtime so the icon cache rescans
    setup_autostart

    if [[ -n $MOVED_DEFAULT ]]; then
        for _ in {1..20}; do   # wait for the app to create the new output
            pw-cli ls Node 2>/dev/null | grep -q '"hifi_stream"' && break
            sleep 0.5
        done
        pw-metadata 0 default.configured.audio.sink '{ "name": "hifi_stream" }' Spa:String:JSON >/dev/null
        echo "Installed, and \"Hi-fi stream (24-bit FLAC)\" is now your sound output."
        return
    fi
    echo "Installed. Click the icon in the top bar and pick your player, then choose"
    echo "\"Hi-fi stream (24-bit FLAC)\" as the sound output and play something."
}

# Everything runs from here, so a partially downloaded script does nothing.
main() {
    case ${1:-} in
        --uninstall) uninstall ;;
        "") install_app ;;
        *) echo "usage: install.sh [--uninstall]" >&2; exit 2 ;;
    esac
}

main "$@"
