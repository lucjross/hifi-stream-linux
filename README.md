# hifi-stream-linux

Stream your Linux desktop's audio to a hi-fi network player as live, lossless 24-bit FLAC.
The player shows up as an ordinary sound output: pick it, press play in any app, and it
plays in the other room.

## Why

Getting lossless audio from a Linux desktop to a streamer on your hi-fi is surprisingly
hard. Each of the usual routes gives something up:

| Route | Lossless | Desktop app's own audio | Catch |
|---|---|---|---|
| Spotify Connect, Tidal Connect, Chromecast | ✅ | ❌ | The player fetches the music itself, so anything the desktop app does (Spotify's **Mix** transitions, an EQ, local files, apps without Connect) never reaches it |
| AirPlay from Linux | ❌ 16-bit | ✅ | PipeWire's AirPlay output is limited to 16-bit/44.1kHz |
| Bluetooth | ❌ | ✅ | Lossy codecs, short range |
| DLNA "stream what you hear" tools | ✅ | ✅ | Manual setup (sample rate, capture device) and starting each session by hand |

This app started as a way to play Spotify's lossless tier *with* Mix transitions on a
[WiiM](https://www.wiimhome.com/) streamer. Spotify Connect gave lossless without Mix, and
AirPlay gave Mix at 16-bit. hifi-stream lets the desktop app do the mixing and sends the
result to the player bit for bit, at 24-bit/44.1kHz. It works the same for any other app.

The goal is that it just works: after installing, the only thing you do is press play.

## How it works

1. Creates a virtual PipeWire output, **Hi-fi stream (24-bit FLAC)**, and runs the audio
   graph at 44.1kHz so music at that rate isn't resampled.
2. When audio starts playing into it, tells the player picked in the tray menu to play
   `http://<this-computer>:8765/stream.flac`.
3. Serves that URL by capturing the output with ffmpeg and encoding it to FLAC on the fly.
4. After 5 minutes without audio, stops the player so it's free for other sources.

The stream works with anything that plays an HTTP FLAC URL. Telling a particular kind of
player to start and stop is the job of a [backend](#players): WiiM is built in, and others
can be added as plugins.

## Requirements

- Linux with PipeWire and `pipewire-pulse` (the default on Ubuntu 22.10+, Fedora 34+ and
  most current distributions)
- A desktop with a system tray: KDE Plasma, Cinnamon, XFCE, MATE, Budgie, and Ubuntu's
  GNOME work out of the box. On stock GNOME (Fedora, Arch) install the
  [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/).
- On Debian/Ubuntu: `sudo apt install ffmpeg avahi-utils pipewire-bin python3-gi gir1.2-ayatanaappindicator3-0.1`

The installer checks these and prints the exact `apt` command for anything missing.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/lucjross/hifi-stream-linux/main/install.sh | bash
```

Or from a clone:

```sh
git clone https://github.com/lucjross/hifi-stream-linux.git
cd hifi-stream-linux
./install.sh
```

This copies the app to `~/.local/bin/hifi-stream` and sets it to start at login: as a
systemd user service on desktops with a systemd session (GNOME, KDE Plasma), otherwise
as an XDG autostart entry. Nothing needs root, apart from installing missing dependencies.

To update, run the install command again. To remove:

```sh
curl -fsSL https://raw.githubusercontent.com/lucjross/hifi-stream-linux/main/install.sh | bash -s -- --uninstall
# or, from a clone: ./install.sh --uninstall
```

## Using it

1. Click the icon in the top bar and pick your player.
2. Choose **Hi-fi stream (24-bit FLAC)** as the sound output (Settings → Sound).
3. Play something. The player starts within a couple of seconds.

The tray menu also shows what's happening ("Streaming 24-bit/44.1kHz", "Waiting for
audio", ...), rescans for players, and toggles starting at login.

## Settings

`~/.config/hifi-stream/config.json`, created with defaults on first run. Open it from the
tray menu with **Edit settings…**, then choose **Restart (apply settings)**.

| Key | Default | |
|---|---|---|
| `port` | `8765` | HTTP port the player fetches the stream from |
| `sink_name` | `"hifi_stream"` | PipeWire name of the virtual output |
| `sink_description` | `""` | Name shown in Sound settings; empty means "Hi-fi stream (24-bit FLAC)" |
| `sample_rate` | `44100` | Rate of the stream. 44.1kHz suits most music, including Spotify; use `48000` if you mostly play video |
| `bit_depth` | `24` | `24` or `16` |
| `idle_release_minutes` | `5` | Stop the player after this long without audio; `0` never stops it |
| `devices` | `{}` | Players to list even if discovery misses them: `{"Kitchen": "192.168.1.50"}` for a WiiM, or `{"Kitchen": {"backend": "...", "address": "..."}}` |

The selected player and discovered devices are kept in `~/.local/state/hifi-stream/state.json`.

## Players

### WiiM (built in)

WiiM players, and probably other LinkPlay-based ones (Arylic, Audio Pro, ...), found with
mDNS and controlled through the
[HTTP API](https://www.wiimhome.com/pdf/HTTP%20API%20for%20WiiM%20Products.pdf).

To play on several WiiMs at once, group them in the WiiM Home app and pick the group
leader. Make your best player the leader: WiiM sends the group's followers a
16-bit/48kHz copy, so only the leader gets the full 24-bit stream.

### Adding one

Put a Python file in `~/.config/hifi-stream/backends/`. `Backend` and `register` are
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
addresses are URLs. Restart from the tray menu to load the plugin. UPnP/DLNA renderers,
Squeezebox and Snapcast would all fit this shape.

## Behaviour and limits

- **Only as good as the source:** the stream is lossless, so lossy sources (YouTube,
  Spotify's non-lossless tiers) arrive exactly as they are, no better. Sources at another
  rate than `sample_rate` are resampled by PipeWire.
- **Delay:** about 1.5–2 seconds on a WiiM between pressing play/pause/skip and hearing
  it, mostly the player's own buffer. Fine for listening, not for live DJing.
- **Taking over the player:** it only starts the player when playback *starts* on the
  desktop. If you switch the player to another source while the desktop keeps playing,
  it's left alone until you next press play on the desktop.
- **Volume:** the desktop volume slider for the virtual output works. At 24-bit, the
  digital attenuation is inaudible. The player's own volume still applies too.
- **Clock drift:** the computer and the player run on separate clocks, and nothing keeps
  them in sync. In long sessions the delay can creep slightly. It resets whenever the
  stream restarts.
- **Firewall:** the player has to reach this computer on the configured port.
- **Tested with:** a WiiM Mini and a WiiM Ultra, on Ubuntu (GNOME) with PipeWire 1.6.

## Command line

```sh
hifi-stream             # tray app (what runs at login)
hifi-stream serve       # virtual output and stream server only, no tray
hifi-stream play IP     # point a WiiM at the stream once (--backend NAME for others)
journalctl --user -u hifi-stream -f   # logs, when running as a systemd service
```

## License

MIT
