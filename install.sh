#!/usr/bin/env bash
# Install wiim-stream for the current user and start it (it then runs at every login).
#   ./install.sh               install or update
#   ./install.sh --uninstall   remove (keeps ~/.config/wiim-stream)
set -euo pipefail

BIN="$HOME/.local/bin/wiim-stream"
ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/wiim-stream-symbolic.svg"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/wiim-stream.service"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$REPO/wiim_stream.py"
PY=/usr/bin/python3   # the distro Python, which has the GTK bindings

if [[ ${1:-} == --uninstall ]]; then
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
    exit 0
fi

missing=()
need() { command -v "$1" >/dev/null || missing+=("$2"); }
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

install -Dm755 "$SRC" "$BIN"
install -Dm644 "$REPO/icons/wiim-stream-symbolic.svg" "$ICON"
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

echo "Installed. Look for the speaker icon in the top bar, pick your WiiM, then choose"
echo "\"WiiM (24-bit FLAC stream)\" as the sound output and play something."
