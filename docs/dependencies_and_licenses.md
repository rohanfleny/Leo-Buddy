# Dependencies and component licenses

This inventory separates files distributed in the repository from tools and
libraries users install. The project source does not vendor the third-party
packages below.

## Project source

| Component | Current release treatment | Attribution/license status |
|---|---|---|
| `firmware/LeoBuddy/LeoBuddy.ino` | Included | Adapted from `playfultechnology/esp32-eyes`; GPL-3.0-only notice and full license are adjacent. Upstream file notice credits Alastair Aitchison / Playful Technology. See [license notes](license_and_credits.md), including the separate AGPL header in upstream `EyeBlink.cpp`. |
| Raspberry Pi app, reminder modules, and tests | Included | No license header/history was found. Keep unlicensed until the owner confirms provenance and chooses a license. Recommended: MIT only if these files are the owner's/Codex-produced original work and no code was copied from an unlicensed DeskBuddy source. |
| README and documentation | Included | Project documentation; its redistribution license should be covered by the owner-approved project/app license or explicitly stated separately. No separate third-party text was copied beyond short factual references and links. |

## Installed dependencies and tools

| Dependency | Evidence/version checked | License/attribution | Distributed here? |
|---|---|---|---|
| OpenAI Python SDK | `openai==2.28.0` in the development environment and pinned in `requirements.txt` | Apache-2.0; OpenAI | No. Installed by pip. |
| U8g2 | 2.36.19 from local library metadata | BSD-2-Clause; Oliver Kraus (`olikraus`) | No. Install separately through Arduino Library Manager/CLI. |
| Arduino ESP32 core | 3.3.12 in the firmware build environment | Arduino-ESP32 repository is LGPL-2.1 licensed | No. Installed as the board core. |
| Python standard library | Python 3.13.5 used for checks; project requires Python 3.10+ | Python Software Foundation license notices accompany Python | No. Part of Python runtime. |
| `arecord`, `ffmpeg`/`ffprobe`, `mpg123` | External executable calls in app/launcher | OS-package licenses vary by package/build; use the target distro's notices | No. Install from the Raspberry Pi OS package manager. |
| Arduino CLI | Build tool only | Not redistributed by this project | No. |
| OpenAI API | External service called by the app | Provider service terms apply; API key and usage charges are separate from source-code licensing | No. |

The U8g2 upstream license notes that some included fonts can have different
terms. Leo Buddy's sketch uses U8g2 drawing primitives and does not bundle the
library or a font asset. If a library or binary is later copied into this
repository, include that package's applicable notices and inspect all bundled
font/data licenses first.

## Codex-generated output and rights

OpenAI's Terms of Use state that, as between the user and OpenAI and to the
extent permitted by law, the user owns Output; they also warn that output may
not be unique. This does not clear third-party material that was supplied as
input or otherwise adapted. See the [official terms](https://openai.com/policies/row-terms-of-use/).

## Before publication

1. Confirm the Raspberry Pi application was not copied from the DeskBuddy
   repository or another source without a redistribution license.
2. Choose and add a license for the Raspberry Pi application and project docs.
   MIT is a simple option for original code if the owner approves it; GPL-3.0
   is another possible choice, but must not be represented as an upstream
   requirement for unrelated original files.
3. Confirm that no code specifically from the AGPL-noticed upstream
   `EyeBlink.cpp` was copied into the firmware. If it was, ask for a licensing
   review before release.
4. Do not add OpenAI API keys, audio recordings, generated audio, runtime
   databases, or local configuration to the repository.
