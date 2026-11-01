#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${OPENAI_API_KEY:-}" && -f "$HOME/.bashrc" ]]; then
  export OPENAI_API_KEY="$(sed -n 's/^export OPENAI_API_KEY="\(.*\)"$/\1/p' "$HOME/.bashrc" | tail -n 1)"
fi
if [[ -z "${LEO_MIC_DEVICE:-}" && -f "$HOME/.bashrc" ]]; then
  configured_mic_device="$(sed -n -E "s/^export LEO_MIC_DEVICE=['\"](.*)['\"]$/\1/p" "$HOME/.bashrc" | tail -n 1)"
  if [[ -n "$configured_mic_device" ]]; then
    export LEO_MIC_DEVICE="$configured_mic_device"
  fi
fi
if [[ -z "${LEO_TTS_VOICE:-}" && -f "$HOME/.bashrc" ]]; then
  configured_tts_voice="$(sed -n -E "s/^export LEO_TTS_VOICE=['\"](.*)['\"]$/\1/p" "$HOME/.bashrc" | tail -n 1)"
  if [[ -n "$configured_tts_voice" ]]; then
    export LEO_TTS_VOICE="$configured_tts_voice"
  fi
fi
export LEO_MIC_DEVICE="${LEO_MIC_DEVICE:-plughw:CARD=Device,DEV=0}"
export LEO_TTS_VOICE="${LEO_TTS_VOICE:-cedar}"
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is not set. Add it to ~/.bashrc first." >&2
  exit 1
fi
source "$PROJECT_DIR/.venv/bin/activate"
exec python "$PROJECT_DIR/voice_demo.py"
