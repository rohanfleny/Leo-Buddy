# Leo Buddy architecture

## Responsibilities

| Component | Responsibility |
|---|---|
| Raspberry Pi application (`voice_demo.py`) | Owns the USB serial link; starts sessions from accepted switch events; captures microphone audio; performs transcription, answer generation, and speech synthesis through the configured OpenAI API; plays audio; runs local reminders; manages the 10-second no-speech-onset timeout. |
| ESP32 sketch (`firmware/LeoBuddy/LeoBuddy.ino`) | Reads/debounces GPIO23; immediately changes its local face on an idle edge; draws the animated OLED face; drives the GPIO18/19 servo outputs; exchanges newline-delimited serial messages. |
| SSD1306 OLED | Presents the five visual states and continuous animated eyes. |
| Switch and servos | Physical user input and bounded pan/tilt movement. The project source assumes a switch to ground with an internal pull-up and externally powered servos. |
| OpenAI API | Remote speech transcription, text response generation, and text-to-speech. Internet and a valid API key are required for these stages. |

## Data path

```mermaid
sequenceDiagram
  participant U as User
  participant S as GPIO23 switch
  participant E as ESP32
  participant P as Raspberry Pi app
  participant A as OpenAI API
  participant M as Microphone / speaker
  U->>S: Toggle causes a debounced electrical edge
  S->>E: GPIO transition
  E->>E: If local state is IDLE, select LISTENING pose
  E-->>P: SWITCH:EDGE:HIGH_TO_LOW or LOW_TO_HIGH\n(115200 USB serial, newline)
  P->>P: Accept exactly once only while Pi state is IDLE
  P->>E: LISTENING\n
  P->>A: Generate/obtain listening greeting audio
  A-->>P: Audio data
  P->>M: Play greeting; then capture user speech
  M-->>P: Recorded PCM/WAV
  P->>A: Transcribe captured speech
  A-->>P: Non-empty transcript
  P->>E: THINKING\n
  P->>A: Generate answer and TTS audio
  A-->>P: Response text/audio
  P->>E: SPEAKING\n when playback begins
  P->>M: Play answer
  P->>E: LISTENING\n after playback completes
  Note over P: 10 s without speech onset while LISTENING ends the session
  P->>E: IDLE\n
```

## Conversation states

The Pi sends uppercase state lines terminated by LF: `IDLE`, `LISTENING`,
`THINKING`, `SPEAKING`, `ERROR`. The app sends `IDLE` and `SYNC` on startup.
`SYNC` asks for current switch level only and does not start a conversation.
An accepted switch event starts one worker if the Pi is idle; further edges
during an active session are ignored. Successful answer playback returns to
LISTENING; each new listening window gets its own 10-second speech-onset
deadline. An empty capture or empty transcript cannot progress to THINKING.

The ESP32 changes its local face to LISTENING immediately on an edge while its
own state is IDLE, then reports the edge. This local update does not prove the
Pi received it. The Pi logs event receipt and acceptance and controls the
conversation worker. On timeout, the Pi sends IDLE; ESP32 sync-level reports
are not session requests.

## OLED and servo firmware

The selected sketch uses U8g2's `U8G2_SSD1306_128X64_NONAME_F_HW_I2C` driver,
sets the I2C address after probing 0x3C/0x3D, and uses GPIO21/22. `drawFace()`
calls `blinkAmount()` on each scheduled frame; eyelid close/hold/open and next
blink scheduling use `millis()` timing. The sketch uses a full-buffer
`sendBuffer()` update at a nominal 34 ms frame interval. That transfer is a
synchronous I2C operation, so the loop is not mathematically non-blocking;
serial, switch, and servo work runs between display frames. The animation
counter is diagnostic evidence of code execution, not visual confirmation.

State-associated targets in source are clamped to 85–95°: IDLE 90/90,
LISTENING 90/95, THINKING starts 95/85 with a bounded pan variation, SPEAKING
cycles bounded pan/tilt poses, and ERROR 85/85. These are requested angles;
physical direction/clearance depend on servo installation.

## Persistent data and network

Reminder data is stored in SQLite under
`~/.local/share/leo/reminders.sqlite3`; conversation context is stored under
`~/.local/state/leo/conversation.json` unless `LEO_CONVERSATION_FILE` overrides
it. Neither database is required in Git. The program uses OpenAI API calls for
transcription (`gpt-transcribe`), answer generation (`gpt-4o-mini`), and TTS
(`gpt-4o-mini-tts` with the configured voice, default `cedar`). Local clock
answers and reminder commands can be handled without the text-generation call,
but the voice pipeline still relies on generated speech for audio prompts.
