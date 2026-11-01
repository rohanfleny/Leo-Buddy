# Release audit

Audit scope: current files in the owner's project folder, selected application
sources/tests, local firmware candidates, the existing deployment notes, and
the visible root/source pages of the referenced GitHub repositories. No files
were removed from or moved out of the working project.

## Findings

- The root folder is **not a valid Git repository**: `git rev-parse`, status,
  and remote inspection report “not a git repository”. A `.git` placeholder
  exists but contains no files and is not writable/usable as repository
  metadata. There are no local branches, remotes, commits, or Git history to
  preserve or scan.
- The voice app entry point is `voice_demo.py`; it imports the local
  `reminder_commands.py`, `reminder_scheduler.py`, and `reminder_store.py`, and
  the third-party `openai` package. The launcher is `run_voice_demo.sh`.
- The active release sketch selected from evidence is the U8g2
  `leo_deskbuddy_open_source_eyes.ino`: its exact `LEO:ANIM`, `LEO:STATE`, and
  `LEO:SWITCH_EDGE` output formats match the Serial Monitor image supplied by
  the owner. The curated copy under `firmware/LeoBuddy/LeoBuddy.ino` is
  byte-for-byte copied from it. This is strong source-to-photo evidence, not a
  readback of the currently flashed binary.
- The sketch implements SSD1306 128×64, address probing 0x3C/0x3D, SDA/SCL
  21/22, U8g2 full-buffer frame updates, five states, GPIO23 switch debounce,
  servo signals 18/19, 85–95° software bounds, and 115200-baud USB Serial.
- The Pi app owns the full-duplex USB serial connection, receives switch edges,
  accepts a session only in IDLE, plays its greeting once, captures/transcribes
  speech, only enters THINKING for usable text, returns to LISTENING after each
  answer, and ends a listening window after 10 seconds without speech onset.
- Runtime external commands: `arecord`, `ffmpeg`, `ffprobe`, `mpg123`. Runtime
  Python package: `openai`. Firmware dependency: U8g2 2.36.19 and an ESP32
  Arduino core. Actual Pi OS/audio-board version is not recorded.
- Current tree contains many alternative `.ino` candidates, test sketches,
  33 MB of timestamped backups, 42 MB Python venv, 146 MB Arduino CLI/library
  cache, Arduino build outputs, old GPIO17 test scripts, generated WAV/MP3
  outputs, and older deployment/handoff notes. These remain untouched in the
  original folder and are excluded from the curated repository.
- No project photos, wiring diagrams verified for this ESP32 setup, or demo
  video were found in the audited project tree. The upstream Pico schematic is
  not reusable as an ESP32 wiring diagram.
- The user-specific `button-voice-demo.service` in the working folder contains
  a personal home path and is not copied. A generic example is in `deploy/`.

## Secret/privacy scan

- Searched source, documentation, and backups for recognizable OpenAI/GitHub/
  AWS token patterns and credential-like filenames. No recognizable token
  values or credential files were found; matches were not printed.
- Searched source/docs for private RFC1918 IPv4 literals. None were found in
  those text files.
- Runtime API keys are expected from environment or the Pi user's shell
  configuration. Neither `.bashrc` nor other files outside the project were
  inspected. Do not publish those files.
- Because the original folder has no Git history, historical-secret scanning
  could not be performed. The curated release Git repository begins with no
  imported history, reducing that exposure; it does not prove that secrets
  were never shared elsewhere.
- Generated `input.wav`, `reply*.mp3`, prompt audio, databases, caches, backups,
  and build products are excluded by the release `.gitignore` or omitted from
  the curated copy. Do not add private voice recordings.

## Curated repository contents

Included: application entry point, directly imported reminder modules,
launcher, one firmware candidate, relevant automated tests, requirements,
systemd example, setup/architecture/wiring/troubleshooting/BOM/release/media/
publishing documentation.

Excluded from the public copy but retained locally: old firmware alternatives,
firmware flash/build binaries, recovery backups, serial/UART test programs,
GPIO17 button experiments, optional UNO JSON controller, all audio artifacts,
virtualenv, Arduino CLI caches, local user-specific service, and obsolete or
duplicated handoff documents. The optional `arduino_controller.py` is not
imported by the voice app and speaks a separate future UNO JSON protocol.

## Release blockers

1. Owner must confirm whether code specifically from the separately
   AGPL-noticed upstream `EyeBlink.cpp` was used and choose a license for the Pi
   application.
2. Owner should review the curated files and confirm the release sketch is the
   intended currently working firmware.
3. Add real, authorized photographs and a demonstration video if desired.
4. Review personal greetings, filenames, and hardware descriptions for public
   comfort and accuracy.
5. No remote has been created, no code has been pushed, and repository
   visibility remains unchanged.
