# wiim-stream

Play your Linux desktop's audio on a network player as a live, lossless 24-bit FLAC
stream, picked from a tray menu. [WiiM](https://www.wiimhome.com/) players are supported
out of the box, and other kinds can be added as [backends](#backends).

Useful whenever the desktop app does something the player can't do by itself. The original
case is the Spotify desktop app: Spotify Connect to a speaker gets you lossless but no
**Mix** transitions, and AirPlay from Linux gets you Mix but only 16-bit. With wiim-stream,
the desktop app does the mixing and the player gets the full 24-bit/44.1kHz signal.

## How it works

1. Creates a virtual PipeWire output, **WiiM (24-bit FLAC stream)**, running at 44.1kHz so
   nothing is resampled.
2. When audio starts playing into it, tells the player you picked to play
   `http://<this-computer>:8765/stream.flac`.
3. Serves that URL by capturing the output with ffmpeg and encoding it to FLAC on the fly.
4. After 5 minutes without audio, stops the player so it's free for other sources.

The stream itself works with anything that plays an HTTP FLAC URL; a backend only has to
find players and tell them to start and stop.

## Requirements

- Linux with PipeWire and `pipewire-pulse` (Ubuntu 22.10 and later by default)
- A GNOME top bar with AppIndicator support (Ubuntu has it built in), or another desktop
  with a system tray
- `sudo apt install ffmpeg avahi-utils pipewire-bin python3-gi gir1.2-ayatanaappindicator3-0.1`

The installer checks these and prints the exact `apt` command for anything missing.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/lucjross/wiim-stream/main/install.sh | bash
```

Or from a clone:

```sh
git clone https://github.com/lucjross/wiim-stream.git
cd wiim-stream
./install.sh
```

Either way, this copies the app to `~/.local/bin/wiim-stream` and sets it up as a systemd
user service that starts at login. Nothing needs root, apart from installing any missing
dependencies. Then:

1. Click the W icon in the top bar and pick your player.
2. Choose **WiiM (24-bit FLAC stream)** as the sound output (Settings → Sound).
3. Play something. The player starts within a couple of seconds.

To update, run the same install command again. To remove:

```sh
curl -fsSL https://raw.githubusercontent.com/lucjross/wiim-stream/main/install.sh | bash -s -- --uninstall
# or, from a clone: ./install.sh --uninstall
```

## Settings

`~/.config/wiim-stream/config.json`, created with defaults on first run. Open it from the
tray menu with **Edit settings…**, then choose **Restart (apply settings)**.

| Key | Default | |
|---|---|---|
| `port` | `8765` | HTTP port the player fetches the stream from |
| `sink_name` | `"wiim_stream"` | PipeWire name of the virtual output |
| `sink_description` | `""` | Name shown in Sound settings; empty means "WiiM (24-bit FLAC stream)" |
| `sample_rate` | `44100` | Spotify's native rate. Change it only if your source uses another rate |
| `bit_depth` | `24` | `24` or `16` |
| `idle_release_minutes` | `5` | Stop the player after this long without audio; `0` never stops it |
| `devices` | `{}` | Players to list even if discovery misses them: `{"Kitchen": "192.168.1.50"}` for a WiiM, or `{"Kitchen": {"backend": "...", "address": "..."}}` |

The selected player and discovered devices are kept in `~/.local/state/wiim-stream/state.json`.

## Backends

### WiiM (built in)

WiiM players, and probably other LinkPlay-based ones (Arylic, Audio Pro, ...), found with
mDNS and controlled through the
[HTTP API](https://www.wiimhome.com/pdf/HTTP%20API%20for%20WiiM%20Products.pdf).

To play on several WiiMs at once, group them in the WiiM Home app and pick the group
leader. Pick your best player as the leader: WiiM sends the group's followers a 16-bit/48kHz
copy, so only the leader gets the full 24-bit stream.

### Adding one

Put a Python file in `~/.config/wiim-stream/backends/`. `Backend` and `register` are
predefined there:

```python
@register
class Example(Backend):
    name = "example"        # used in settings
    label = "Example"       # shown in the menu

    def discover(self):     # optional: {display name: address}
        return {}

    def play(self, address, url):
        ...                 # tell the player at address to play url

    def stop(self, address):
        ...
```

`host(address)` must return the IP the player connects from, so the app can tell when it's
streaming. The default resolves `address` as a hostname or IP; override it if your
addresses are URLs. Restart from the tray menu to load the plugin.

## Behaviour and limits

- **Delay:** about 1.5–2 seconds on a WiiM between pressing play/pause/skip and hearing
  it, mostly the player's own buffer.
- **Taking over the player:** it only starts the player when playback *starts* on the
  desktop. If you switch the player to another source while the desktop keeps playing,
  it's left alone until you next press play on the desktop.
- **Volume:** the desktop volume slider for the virtual output works. At 24-bit, the
  digital attenuation is inaudible. The player's own volume still applies too.
- **Clock drift:** the computer and the player run on separate clocks, and nothing keeps
  them in sync. In long sessions the delay can creep slightly. It resets whenever the
  stream restarts.
- **Firewall:** the player has to reach this computer on the configured port.
- **Tested with:** a WiiM Mini and a WiiM Ultra, on Ubuntu with PipeWire 1.6.

## Command line

```sh
wiim-stream             # tray app (what the service runs)
wiim-stream serve       # virtual output and stream server only, no tray
wiim-stream play IP     # point a WiiM at the stream once (--backend NAME for others)
journalctl --user -u wiim-stream -f   # logs
```

## License

MIT
