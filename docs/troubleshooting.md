# Troubleshooting

## OLED is blank or not animating

1. Confirm the sketch in Arduino IDE is `firmware/LeoBuddy/LeoBuddy.ino`, the
   target is ESP32 Dev Module, and the upload completed.
2. Check the startup `LEO:OLED:READY` line and address in Serial Monitor at
   115200 baud. The sketch probes 0x3C and 0x3D and expects SSD1306 128×64.
3. Recheck SDA21/SCL22/GND and the OLED breakout supply rating. A powered
   display is not proof that I2C communication or the correct controller is
   present.
4. `LEO:ANIM` frame/blink counters show scheduler activity only; look at the
   physical screen to confirm visible eyelid changes.
5. If using Arduino Serial Monitor, send `IDLE` with newline. Do not keep it
   open while the Pi app owns USB serial.

## Pi cannot connect to the ESP32 serial port

1. Close Arduino Serial Monitor and stop any second voice-app process.
2. Confirm the USB cable carries data and inspect `ls -l /dev/serial/by-id/`
   and `ls -l /dev/ttyUSB*`.
3. Check the user is in `dialout` (`groups`) and reconnect/log in after adding
   a group. The application auto-discovers the CP210x VID/PID when possible,
   preferring the stable by-id path; `LEO_UART_PORT` overrides it.
4. Watch `journalctl -u leo-buddy.service -f` for connection/reconnect and
   switch-event receipt messages. A Serial Monitor edge line by itself does
   not prove the Pi received the event.
5. Firmware diagnostics are ignored unless they match the exact switch event
   protocol. If “discarded oversized serial line” appears, check the firmware
   for very long newline-free output; the Pi parser has a bounded line size.

## Toggle does not start a session

- Source expects GPIO23 with `INPUT_PULLUP`, normally closed to ESP32 GND.
- Confirm startup `SWITCH:STATE:HIGH/LOW` sync is not mistaken for an edge.
- A debounced edge while Pi state is IDLE should appear as
  `SWITCH:EDGE:HIGH_TO_LOW` or `SWITCH:EDGE:LOW_TO_HIGH` and then a Pi-side
  “accepted” / “starting” log. An edge during a session is intentionally
  ignored. After timeout/IDLE, a new physical transition is required.
- Check the physical contact goes to GND, not 5 V, and ensure the switch is
  not floating or wired to a different GPIO.

## Microphone is silent, too quiet, or the transcript is empty

Run `arecord -l` and `aplay -l` on the Pi. Compare capture cards against
`LEO_MIC_DEVICE`; try a short manual capture using the selected device, 16 kHz,
mono, signed 16-bit PCM, then inspect the WAV's duration and level. The app
logs capture duration, RMS/peak level, onset detection, and transcription
result. Avoid speaker feedback into the microphone. An empty transcript stays
in LISTENING and must not generate an answer; the current 10-second deadline
ends the session if no usable speech begins. API/network failures are distinct
from a silent recording.

## Speaker output or voice differs

Check `aplay -l`, the configured ALSA default device, amplifier power, mixer
level, and speaker connection. `mpg123` plays processed TTS audio to ALSA.
The voice and sound are controlled by `LEO_TTS_VOICE` and current model/API
behavior; this project does not bundle an offline voice model.

## Servo does not move, jitters, or strains

- Confirm GPIO18/19 are connected to signal leads, not supply rails.
- Use a regulated external 5 V supply sized for both actual servos; tie its
  ground to ESP32 GND. Do not use 3.3 V for servo power.
- Check mechanical clearance and linkage orientation. The source target range
  is intentionally narrow (85–95°), not the servo's full theoretical range.
- Confirm state commands in app logs and `LEO:STATE` diagnostics; requested
  angle does not prove shaft movement.

## Missing credentials, dependencies, or startup failure

- `OPENAI_API_KEY is not set`: add it privately to the environment or expected
  `.bashrc` export line; never paste it into GitHub or terminal screenshots.
- `arecord`, `ffmpeg`, `ffprobe`, or `mpg123` missing: install the OS packages
  in the setup guide and verify with `command -v`.
- Python import error: activate the project's `.venv` and install
  `requirements.txt`.
- systemd failure: `systemctl status leo-buddy.service` and
  `journalctl -u leo-buddy.service -b`; verify service username/path, key
  configuration, network, audio groups, and serial permissions.
- Wi-Fi auto-connect is OS-managed. Verify saved network status with
  `nmcli device status`; API calls need internet access.
