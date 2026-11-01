# Component licensing and attribution

Licenses are scoped by component. The adapted ESP32 firmware is marked
GPL-3.0-only in `firmware/LeoBuddy/LICENSE`; the Raspberry Pi application has
not been assigned a license. No repository-root `LICENSE` is present, so the
firmware license should not be read as automatically relicensing the Pi code.

## Upstream references inspected

- The original Raspberry Pi AI DeskBuddy repository is
  [`sparklabhqx/RPI_AI_DeskBuddy_v.1.0`](https://github.com/sparklabhqx/RPI_AI_DeskBuddy_v.1.0).
  Its visible root file list contains a setup prompt, Pi demo, Pico controller,
  and schematic images, but no visible `LICENSE` file. The local code's exact
  provenance relative to that Pi demo has not been established. Obtain
  permission or confirm the local Pi code was independently authored before
  redistributing any copied portion.
- The owner confirms the selected eye-animation firmware was adapted from
  [`playfultechnology/esp32-eyes`](https://github.com/playfultechnology/esp32-eyes).
  The GitHub repository is marked GPL-3.0 and its main `esp32-eyes.ino` credits
  [`Copyright (c) 2023 Alastair Aitchison, Playful Technology`](https://github.com/playfultechnology/esp32-eyes/blob/main/esp32-eyes.ino).
  Its main example creates a `Face(128, 64, 40)`, enables `RandomBlink`, sets a
  4000 ms blink interval, and calls `Face::Update()` continuously. Leo adapts
  the parameterized eye/automatic-blink behavior but uses its own single-file
  U8g2 renderer: `blinkAmount()` schedules 2200–4300 ms intervals and eased
  close/hold/open phases, while `drawEye()`/`drawFace()` render five Leo states.
  No upstream `Face.*` or `EyeBlink.*` implementation files/API are bundled.
  The staged firmware is marked GPL-3.0-only based on the repository's
  GPL-3.0 license and the owner's confirmation that it is adapted. The upstream
  [`EyeBlink.cpp`](https://github.com/playfultechnology/esp32-eyes/blob/main/EyeBlink.cpp)
  separately carries a 2020 copyright notice for Luis Llamas and an
  AGPL-3.0-or-later header. The custom staged sketch does not contain that
  source file; if the owner knows code specifically from it was copied or
  modified, licensing needs reassessment. Preserve both upstream credits in
  documentation, and do not imply either upstream author wrote Leo Buddy or
  its integration.
- U8g2 2.36.19 declares BSD-2-Clause in its library metadata. It is installed
  as a build dependency, not vendored in this repository. Keep its notices
  available when distributing the dependency itself.
- The OpenAI Python SDK declares Apache-2.0 in installed package metadata; it
  is installed through `requirements.txt`, not copied into this repository.

## Remaining owner decisions

1. Confirm whether code specifically from upstream `EyeBlink.cpp` was copied or
   modified. The staged sketch has no upstream class files and uses a separate
   renderer, but the old local source history is unavailable, so the history of
   how the implementation was developed cannot be reconstructed independently.
2. Choose a separate license for the Raspberry Pi Python application, or
   explicitly keep it unlicensed/all-rights-reserved. Confirm the Pi code was
   not copied from the DeskBuddy repository, whose inspected root had no
   visible license.
3. Confirm the final attribution wording and whether you want your own
   copyright line added. No author/year line for local modifications has been
   invented.

The firmware source and the upstream project's full GPL-3 license text are
included in `firmware/LeoBuddy/`. This is release preparation, not legal
advice. Do not publish until the remaining component provenance and Pi-code
license decisions are resolved. See
[`dependencies_and_licenses.md`](dependencies_and_licenses.md) for the separate
SDK, display library, board core, and system-tool inventory.
