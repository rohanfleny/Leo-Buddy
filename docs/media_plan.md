# Media and demonstration video plan

## Existing media audit

No project photographs, wiring images, OLED captures, or demonstration video
were present in the audited project directory. Runtime `input.wav` and reply
MP3 files are not suitable project media and must stay private/excluded. The
upstream repository's schematic is for a Pico-based design and must not be
presented as Leo Buddy's ESP32 wiring.

## Capture checklist

1. **Front hero photo:** robot fully assembled, clean background, OLED eyes
   clearly visible; avoid a reflection that hides the display.
2. **Hardware overview:** Pi, ESP32 enclosure, OLED, switch, servos, microphone,
   and audio components; show external servo supply safely and label it.
3. **Wiring close-up:** only after physically tracing every connection. Show
   signal, power, ground, and USB distinctly; remove unrelated private items.
4. **OLED states:** capture IDLE blinking and each state (LISTENING, THINKING,
   SPEAKING, ERROR). For a GIF, keep it short and optimized; verify it visibly
   shows motion after compression.
5. **Voice demo:** record only with consent from everyone whose voice is
   audible. Do not show API keys, Wi-Fi credentials, private reminders, or
   home-network details in terminal views.

Use genuine project media only. Crop/resize web images to an optimized width
around 1200 px for README rather than adding original camera files. Keep a
short GIF or compressed stills in the repository; link the full video hosted
externally rather than committing a large MP4. GitHub warns on large individual
files and imposes repository limits; keep source assets modest and use a video
platform for the full demonstration.

## Suggested repository media layout after capture

```text
media/
  leo-buddy-front.jpg
  leo-buddy-hardware.jpg
  oled-expressions.gif
```

Do not create empty media directories. Add them only when authorized assets
exist. Add the external video URL to the README and GitHub repository homepage
field after it is published.
