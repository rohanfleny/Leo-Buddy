# LinkedIn drafts (review before publishing)

## Recommendation

Use a two-post sequence: first publish a short real demonstration video with
the completed robot and a concise conversation; about one week later, publish
a technical post explaining the integration and lessons learned. The first
post makes the result tangible; the second gives engineering detail without
overloading the demo. Do not claim offline operation or unverified features.

## Post A — Project demonstration

**Suggested length:** about 100–150 words. Attach a 30–45 second video.

> I built Leo Buddy, a small desk companion that combines a Raspberry Pi voice
> assistant with an ESP32-controlled animated face.
>
> A physical switch starts a conversation. The Pi handles speech capture and
> AI-generated spoken replies, while the ESP32 updates the OLED expressions
> and moves two servos with the assistant's state. Getting the Pi, USB serial
> protocol, audio pipeline, and animated display to work together took much
> more debugging than I expected—but seeing the complete interaction come
> together made the project worth it.
>
> I’m a third-year Electronics and Communication Engineering student, and
> this build has been a great hands-on lesson in embedded systems and
> hardware/software integration.
>
> Project details: [GitHub repository URL]

Suggested hashtags: `#EmbeddedSystems #RaspberryPi #ESP32 #Robotics
#ElectronicsEngineering`.

**Video opening:** begin with the assembled robot, then show a real toggle,
greeting, one question/answer, and the face/servo states. Do not show a staged
or simulated exchange.

## Post B — Technical showcase

**Suggested length:** about 150–220 words. Publish roughly 5–10 days after
Post A, with a clear hardware photo or a simple architecture diagram.

> A closer look at the engineering behind Leo Buddy:
>
> • Raspberry Pi: microphone capture, speech transcription, response
> generation, text-to-speech, and audio playback.
> • ESP32: a 128×64 SSD1306 face, autonomous eyelid animation, GPIO23 switch
>   handling, and two bounded servo outputs.
> • Link: newline-delimited USB serial at 115200 baud, with Pi-to-ESP32 state
>   messages and ESP32 switch-edge events.
>
> One important design lesson was separating responsibilities: the Pi owns
> conversation and audio, while the ESP32 keeps the face, switch, and servo
> loop responsive. I also learned to distinguish a serial diagnostic from an
> end-to-end event—the Pi must actually receive the switch event before a
> session has begun.
>
> The voice features use an external API and require internet access. The
> project and setup notes: [GitHub repository URL]
>
> I’m still documenting the wiring and collecting final project photos.

Hashtags: `#EmbeddedSystems #ESP32 #RaspberryPi #VoiceAssistant
#EngineeringProjects`.

Remove the final sentence when the wiring/media are complete. Both posts are
drafts only; nothing has been published.
