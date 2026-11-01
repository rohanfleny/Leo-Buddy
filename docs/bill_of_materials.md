# Bill of materials (evidence-limited)

This list records parts described by the owner or visible in existing source
and logs. It does not specify prices or suppliers. Exact part numbers are
included only where they have been identified.

| Qty. | Component | Evidence / unresolved detail |
|---:|---|---|
| 1 | Raspberry Pi 4 Model B Rev 1.5 | Owner-reported. Pi OS release and RAM capacity not recorded. |
| 1 | ESP32-WROOM-32 Dev Module board | Owner-reported; exact DevKit manufacturer/revision not recorded. |
| 1 | 128×64 SSD1306-compatible I2C OLED | Owner reports SSD1306 at 0x3C; sketch also probes 0x3D. Breakout board model and VCC tolerance unknown. |
| 2 | SG90 micro servos | Owner-reported. Exact brand/clone, stall current, and measured travel unknown. |
| 1 | Toggle switch | GPIO23 input; switch type, pole count, and enclosure mounting model not recorded. |
| 1 | USB microphone / USB PnP Sound Device | A runtime log shows a USB Audio capture device; exact product model is unknown. |
| 1 | Audio DAC/amplifier path and speakers | Project notes report HiFiBerry DAC and amplifier/speakers; exact board and amplifier models are not verified. |
| 1+ | USB data cable(s) | Needed for Pi↔ESP32 connection; exact cable/model unknown. |
| 1 | Regulated external 5 V servo supply | Required by wiring guidance; actual installed model/current rating must be confirmed. Size for both servo loads. |
| — | Enclosure | Owner reports a 3D-printed enclosure; material and print settings unknown. |

Before publishing a BOM with exact electrical ratings, inspect the actual
labels/datasheets for the OLED breakout, audio hardware, servos, and servo
supply. Do not infer the external supply's current rating from the phrase
“5 V”.
