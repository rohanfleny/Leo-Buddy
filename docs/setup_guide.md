# Setup guide

This guide targets a Debian-based Raspberry Pi OS and the ESP32 Dev Module
used by the current project. The exact Pi OS release and installed audio board
are not recorded, so verify package names/device names on the target Pi.

## 1. Raspberry Pi prerequisites

Use Python 3.10 or newer (the release was checked with Python 3.13.5), a
working network connection, a microphone capture device, an ALSA playback
device, and a USB data connection to the ESP32. Install system tools:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip alsa-utils ffmpeg mpg123
```

FFmpeg supplies `ffmpeg` and typically `ffprobe`. Confirm all runtime tools:

```bash
python3 --version
command -v arecord ffmpeg ffprobe mpg123
arecord -l
aplay -l
```

Select the intended capture device from `arecord -l`. The source default is
`plughw:CARD=Device,DEV=0`; set `LEO_MIC_DEVICE` if the Pi reports a different
ALSA card/device identifier. Do not infer capture-device names from playback
hardware.

## 2. Get the project and install Python dependencies

For the public GitHub copy, clone the repository after it is published. For
the local release-preparation folder, use its root. Then:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

The application uses the `openai` Python package (`2.28.0` in the audited
environment); the rest of its Python imports are from the standard library or
local reminder modules.

## 3. Configure credentials privately

The launcher reads `OPENAI_API_KEY` from its environment or from a line in
`~/.bashrc` formatted as:

```bash
export OPENAI_API_KEY="YOUR_REAL_KEY_GOES_HERE"
```

Add the real key on the Pi only, never in this repository. Protect the file
with `chmod 600 ~/.bashrc` if appropriate for your setup, and never paste the
key into screenshots or logs. The launcher also reads optional
`LEO_MIC_DEVICE` and `LEO_TTS_VOICE` values from the environment or supported
export lines in `.bashrc`.

Configuration used by source:

| Variable | Default / purpose |
|---|---|
| `OPENAI_API_KEY` | Required secret; used by the SDK. |
| `LEO_MIC_DEVICE` | `plughw:CARD=Device,DEV=0`; ALSA capture device. |
| `LEO_UART_PORT` | Auto-discover the CP210x USB UART; otherwise code fallback is `/dev/ttyUSB0`. |
| `LEO_RECORD_SECONDS` | `8` for the fixed-window recording helper; live utterance capture is bounded by the listen deadline. |
| `LEO_TTS_VOICE` | `cedar`. |
| `LEO_TTS_TIMEOUT_SECONDS` | `25`. |
| `LEO_AUDIO_TARGET_LUFS` | `-20`; legacy `LEO_REPLY_TARGET_LUFS` is a fallback. |
| `LEO_AUDIO_TRUE_PEAK_DBTP` | `-1.5`. |
| `LEO_CONVERSATION_FILE` | Optional path override for conversation context. |

## 4. ESP32 firmware

Open `firmware/LeoBuddy/LeoBuddy.ino` in Arduino IDE. Select **ESP32 Dev
Module** (`esp32:esp32:esp32`) and install the ESP32 Arduino board core plus
U8g2 library (the audited build used ESP32 core 3.3.12 and U8g2 2.36.19).
Compile, select the ESP32 USB port, and upload only after reviewing that upload
replaces the application currently installed on the ESP32. Do not power the
servos from 3.3 V; use the external supply arrangement in the wiring guide.

For a command-line build with Arduino CLI already configured:

```bash
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/LeoBuddy
```

To test state rendering from Arduino Serial Monitor, send one uppercase state
per newline. Close Serial Monitor before the Pi app opens the same USB port.

## 5. Run manually

Connect the ESP32 to a Pi USB port with a data cable. Ensure the Pi user has
permission for the CP210x device (commonly group `dialout`). Do not also leave
Arduino Serial Monitor open. Run:

```bash
./run_voice_demo.sh
```

Expected startup includes ESP32 serial connected (or a warning if unavailable),
`Sent state: IDLE`, `Sent state: SYNC`, and “ready in IDLE”. Toggle once. Use
`Ctrl+C` to stop.

## 6. Optional boot-time service

`deploy/leo-buddy.service.example` is a template, not directly installable: edit
the username and repository path to the actual Pi account and clone directory.
The already-working owner-specific service unit is not in this public copy.
Install the edited unit under `/etc/systemd/system/`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now leo-buddy.service
sudo systemctl status leo-buddy.service --no-pager
sudo journalctl -u leo-buddy.service -f
```

Only one process can own the USB serial device. Stop a manual app before
starting the service. Wi-Fi credentials and auto-connect are managed by the
Pi OS, not this application; the Pi must reconnect to a saved network for API
features. Disable the service with `sudo systemctl disable --now
leo-buddy.service`.

## 7. Software tests

```bash
.venv/bin/python -m unittest discover -s tests -v
bash -n run_voice_demo.sh
```

These tests mock network, recording, playback, and serial paths where needed.
They do not test actual audio hardware or the assembled robot.
