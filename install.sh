#!/usr/bin/env bash
# Install wiim-stream for the current user and start it (it then runs at every login).
#
#   ./install.sh                 install or update from this checkout
#   ./install.sh --uninstall     remove (keeps ~/.config/wiim-stream)
#
# Or without cloning, which fetches the latest main from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/lucjross/wiim-stream/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/lucjross/wiim-stream/main/install.sh | bash -s -- --uninstall
set -euo pipefail

TARBALL=${WIIM_STREAM_TARBALL:-https://github.com/lucjross/wiim-stream/archive/refs/heads/main.tar.gz}
BIN="$HOME/.local/bin/wiim-stream"
ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/wiim-stream-symbolic.svg"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/wiim-stream.service"
PY=/usr/bin/python3   # the distro Python, which has the GTK bindings

uninstall() {
    systemctl --user disable --now wiim-stream.service 2>/dev/null || true
    rm -f "$UNIT" "$BIN" "$ICON"
    systemctl --user daemon-reload
    # Remove the virtual output now rather than at the next PipeWire restart
    pw-dump 2>/dev/null | "$PY" -c '
import json, sys
for o in json.load(sys.stdin):
    if o.get("info", {}).get("props", {}).get("wiim_stream.managed"):
        print(o["id"])' | xargs -r -n1 pw-cli destroy >/dev/null || true
    echo "Uninstalled. Settings are left in ${XDG_CONFIG_HOME:-$HOME/.config}/wiim-stream."
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
        echo "This ffmpeg was built without PulseAudio input, which wiim-stream needs." >&2
        exit 1
    fi
}

install_app() {
    check_deps
    # Use this checkout, or download one when the script was piped from curl
    local self=${BASH_SOURCE[0]:-} src
    if [[ -n $self && -f $(dirname "$self")/wiim_stream.py ]]; then
        src=$(cd "$(dirname "$self")" && pwd)
    else
        src=$(mktemp -d)
        trap "rm -rf '$src'" EXIT   # expanded now: src is local and gone by exit
        echo "Downloading wiim-stream…"
        curl -fsSL "$TARBALL" | tar -xz -C "$src" --strip-components=1
    fi
    install -Dm755 "$src/wiim_stream.py" "$BIN"
    install -Dm644 "$src/icons/wiim-stream-symbolic.svg" "$ICON"
    touch "${ICON%/scalable/*}"   # bump the theme dir's mtime so the icon cache rescans

    mkdir -p "$UNIT_DIR"
    cat > "$UNIT" <<EOF
[Unit]
Description=wiim-stream: play desktop audio on a WiiM as lossless FLAC
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
    systemctl --user enable wiim-stream.service
    systemctl --user restart wiim-stream.service

    echo "Installed. Click the W icon in the top bar and pick your WiiM, then choose"
    echo "\"WiiM (24-bit FLAC stream)\" as the sound output and play something."
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
