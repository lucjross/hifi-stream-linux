# wiim-stream

Play your Linux desktop's audio on a [WiiM](https://www.wiimhome.com/) streamer as a live,
lossless 24-bit FLAC stream, picked from a tray menu.

Built for the Spotify desktop app: Spotify Connect to a WiiM gets you lossless but no
**Mix** transitions, and AirPlay from Linux gets you Mix but only 16-bit. With wiim-stream,
the desktop app does the mixing and the WiiM gets the full 24-bit/44.1kHz signal.

## How it works

1. Creates a virtual PipeWire output, **WiiM (24-bit FLAC stream)**, running at 44.1kHz so
   nothing is resampled.
2. When audio starts playing into it, tells the WiiM you picked (via its
   [HTTP API](https://www.wiimhome.com/pdf/HTTP%20API%20for%20WiiM%20Products.pdf)) to play
   `http://<this-computer>:8765/stream.flac`.
3. Serves that URL by capturing the output with ffmpeg and encoding it to FLAC on the fly.
4. After 5 minutes without audio, stops the WiiM so it's free for other sources.

To play on several WiiMs at once, group them in the WiiM Home app and pick the group leader.

## Requirements

- Linux with PipeWire and `pipewire-pulse` (Ubuntu 22.10 and later by default)
- A GNOME top bar with AppIndicator support (Ubuntu has it built in), or another desktop
  with a system tray
- `sudo apt install ffmpeg avahi-utils pipewire-bin python3-gi gir1.2-ayatanaappindicator3-0.1`

The installer checks these and prints the exact `apt` command for anything missing.

## Install

```sh
git clone https://github.com/lucjross/wiim-stream.git
cd wiim-stream
./install.sh
```

This copies the app to `~/.local/bin/wiim-stream` and sets it up as a systemd user service
that starts at login. Then:

1. Click the speaker icon in the top bar and pick your WiiM.
2. Choose **WiiM (24-bit FLAC stream)** as the sound output (Settings → Sound).
3. Play something. The WiiM starts within a couple of seconds.

To update, pull and run `./install.sh` again. To remove: `./install.sh --uninstall`.

## Settings

`~/.config/wiim-stream/config.json`, created with defaults on first run. Open it from the
tray menu with **Edit settings…**, then choose **Restart (apply settings)**.

| Key | Default | |
|---|---|---|
| `port` | `8765` | HTTP port the WiiM fetches the stream from |
| `sink_name` | `"wiim_stream"` | PipeWire name of the virtual output |
| `sink_description` | `""` | Name shown in Sound settings; empty means "WiiM (24-bit FLAC stream)" |
| `sample_rate` | `44100` | Spotify's native rate. Change it only if your source uses another rate |
| `bit_depth` | `24` | `24` or `16` |
| `idle_release_minutes` | `5` | Stop the WiiM after this long without audio; `0` never stops it |
| `devices` | `{}` | WiiMs to list even if discovery misses them, e.g. `{"Kitchen": "192.168.1.50"}` |

The selected WiiM and discovered devices are kept in `~/.local/state/wiim-stream/state.json`.

## Behaviour and limits

- **Delay:** about 1.5–2 seconds between pressing play/pause/skip and hearing it, mostly
  the WiiM's own buffer.
- **Taking over the WiiM:** it only starts the WiiM when playback *starts* on the desktop.
  If you switch the WiiM to another source while the desktop keeps playing, it's left alone
  until you next press play on the desktop.
- **Volume:** the desktop volume slider for the virtual output works. At 24-bit, the
  digital attenuation is inaudible. The WiiM's own volume still applies too.
- **Clock drift:** the computer and the WiiM run on separate clocks, and nothing keeps them
  in sync. In long sessions the delay can creep slightly. It resets whenever the stream
  restarts.
- **Firewall:** the WiiM has to reach this computer on the configured port.
- **Tested with:** a WiiM Mini, and a WiiM Ultra grouped as a follower, on Ubuntu with
  PipeWire 1.6.

## Command line

```sh
wiim-stream             # tray app (what the service runs)
wiim-stream serve       # virtual output and stream server only, no tray
wiim-stream play IP     # point a WiiM at the stream once
journalctl --user -u wiim-stream -f   # logs
```

## License

MIT
