#!/usr/bin/env python3
"""Toggle-controlled OpenAI voice conversation for LEO AI DeskBuddy."""
import math
import json
import hashlib
import os
import re
import select
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import termios
import time
import unicodedata
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from queue import Queue
from reminder_commands import handle_reminder_command
from reminder_scheduler import ReminderScheduler
from reminder_store import ReminderStore

from openai import OpenAI, OpenAIError

PROJECT_DIR = Path(__file__).resolve().parent
INPUT_WAV = PROJECT_DIR / "input.wav"
BOOSTED_WAV = PROJECT_DIR / "input_boosted.wav"
REPLY_MP3 = PROJECT_DIR / "reply.mp3"
REPLY_PLAYBACK_MP3 = PROJECT_DIR / "reply_playback.mp3"
LISTENING_PROMPT_MP3 = PROJECT_DIR / "prompt_listening.mp3"
THINKING_PROMPT_MP3 = PROJECT_DIR / "prompt_thinking.mp3"
CONVERSATION_FILE = Path(os.environ.get("LEO_CONVERSATION_FILE", Path.home() / ".local/state/leo/conversation.json"))
DEFAULT_MIC_DEVICE = "plughw:CARD=Device,DEV=0"
MIC_DEVICE = os.environ.get("LEO_MIC_DEVICE") or DEFAULT_MIC_DEVICE
# USB serial is the active Pi-to-ESP32 transport; override only for a different
# USB serial device path.
DEFAULT_UART_PORT = "/dev/ttyUSB0"
UART_PORT = os.environ.get("LEO_UART_PORT")
RECORD_SECONDS = int(os.environ.get("LEO_RECORD_SECONDS", "8"))
LISTEN_SILENCE_TIMEOUT_SECONDS = 10.0
UTTERANCE_END_SILENCE_SECONDS = 1.2
MIN_UTTERANCE_CAPTURE_AFTER_ONSET_SECONDS = 2.0
PCM_BYTES_PER_SECOND = 16000 * 2
MIN_UTTERANCE_AUDIO_SECONDS = 0.25
# Firmware diagnostics may be longer than the small switch-event lines. Keep
# enough room for them, but still bound memory if a device sends no newline.
MAX_ESP32_SERIAL_LINE_BYTES = 256
AUDIO_TARGET_LUFS = float(os.environ.get(
    "LEO_AUDIO_TARGET_LUFS", os.environ.get("LEO_REPLY_TARGET_LUFS", "-20")
))
AUDIO_TRUE_PEAK_DBTP = float(os.environ.get("LEO_AUDIO_TRUE_PEAK_DBTP", "-1.5"))
TRANSCRIBE_MODEL = "gpt-transcribe"
TEXT_MODEL = "gpt-4o-mini"
TTS_MODEL = "gpt-4o-mini-tts"
TTS_VOICE = os.environ.get("LEO_TTS_VOICE", "cedar").strip() or "cedar"
VOICE_APP_BUILD = "2026-11-01-fixed-window-capture"
LISTENING_TTS_INSTRUCTIONS = (
    "Use the same Cedar speaker identity throughout. Speak in a warm, friendly, relaxed, natural conversational tone, "
    "with normal sentence rhythm and smoothly connected speech. Keep pacing natural; do not insert deliberate pauses "
    "between ordinary words or overemphasize individual words, including names. Avoid robotic emphasis and an "
    "unnecessarily deep or formal delivery. Keep a clear, neutral English accent."
)
TTS_INSTRUCTIONS = (
    "Speak in a warm, friendly, relaxed conversational tone. Use a natural pace and ordinary sentence rhythm, "
    "letting phrases flow smoothly together. Keep the voice clear and approachable, with gentle natural expression. "
    "Avoid dramatic pauses, word-by-word emphasis, robotic delivery, or an overly deep or formal sound."
)
TTS_TIMEOUT_SECONDS = float(os.environ.get("LEO_TTS_TIMEOUT_SECONDS", "25"))
CONVERSATION_TURNS = 4
LISTENING_PROMPT_TEXT = "Hey Rohan! What's up? I'm listening."
THINKING_PROMPT_TEXT = "Alright, let me think."
NO_SPEECH_TEXT = "I didn't catch that. Please try again."
FRIENDLY_BASE_PROMPT = (
    "You are LEO, a friendly, intelligent desktop companion. Be warm, calm, clear, natural, and helpful; "
    "use light humor or a conversational phrase occasionally, never as a repeated formula. "
    "Keep ordinary spoken answers concise, usually 1 to 3 short sentences. Understand follow-up questions "
    "using the supplied conversation context. Do not invent context or over-explain."
)
ENGLISH_INSTRUCTIONS = "Reply in natural English."
NON_ENGLISH_FALLBACK = "Sorry, I couldn't formulate a clear English reply. Please try asking another way."


def onset_deadline_expired(started_at: float, now: float, timeout: float) -> bool:
    """True only when the current waiting-for-speech-onset window has elapsed."""
    return now - started_at >= max(0.0, timeout)


class UartStateSender:
    RECONNECT_INTERVAL_SECONDS = 2.0

    def __init__(self, port: str | None = None):
        self.auto_discover = port is None
        self.port, self.stream = port or DEFAULT_UART_PORT, None
        self.next_open_attempt = 0.0
        self._buffer = bytearray()
        self._discarding_line = False
        self._lock = threading.RLock()
        self.connection_lost = False
        self.display_state = "IDLE"

    def open(self):
        if self.stream:
            return True
        if self.auto_discover:
            self.port = discover_usb_uart_port()
        fd = None
        try:
            fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_SYNC)
            attrs = termios.tcgetattr(fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
            attrs[4] = attrs[5] = termios.B115200
            attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            self.stream = os.fdopen(fd, "r+b", buffering=0)
            fd = None
            self._buffer.clear()
            self._discarding_line = False
            self.connection_lost = False
            self.next_open_attempt = 0.0
            return True
        except OSError:
            self.next_open_attempt = time.monotonic() + self.RECONNECT_INTERVAL_SECONDS
            raise
        finally:
            if fd is not None:
                os.close(fd)

    def send(self, state: str):
        with self._lock:
            if state in {"IDLE", "LISTENING", "THINKING", "SPEAKING", "ERROR"}:
                # Keep the desired display state even while USB is disconnected.
                # On reconnect, SYNC is followed by this latest known state.
                self.display_state = state
            if self.stream is None:
                if time.monotonic() < self.next_open_attempt:
                    return False
                try:
                    self.open()
                except OSError as exc:
                    print(f"WARNING: status serial unavailable on {self.port}: {exc}", flush=True)
                    return False
            try:
                self.stream.write(f"{state}\n".encode("ascii"))
                print(f"Sent state: {state}", flush=True)
                return True
            except OSError as exc:
                self._mark_disconnected()
                print(f"WARNING: status serial write failed on {self.port}: {exc}", flush=True)
                return False

    def poll_events(self) -> list[str]:
        """Read complete newline-delimited ESP32 events without blocking."""
        with self._lock:
            if self.stream is None:
                if time.monotonic() < self.next_open_attempt:
                    return []
                try:
                    self.open()
                except OSError as exc:
                    print(f"WARNING: unable to reopen ESP32 serial port {self.port}: {exc}", flush=True)
                    return []
                print(f"ESP32 USB serial reconnected on {self.port}; syncing switch state", flush=True)
                self.send("SYNC")
                return []
            fd = self.stream.fileno()
            try:
                readable, _, _ = select.select([fd], [], [], 0)
                if readable:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        self._mark_disconnected()
                        return []
                    return self._consume_serial_bytes(chunk)
                return []
            except OSError as exc:
                self._mark_disconnected()
                print(f"WARNING: status serial read failed on {self.port}: {exc}", flush=True)
                return []

    def _consume_serial_bytes(self, chunk: bytes) -> list[str]:
        """Frame UART lines; discard only an overlong line, not later events.

        ESP32 diagnostics are not protocol events. A malformed/oversized
        diagnostic must never clear a following valid SWITCH line from the
        same USB read or leave its tail to be parsed as a false command.
        """
        events = []
        for byte in chunk:
            if byte == 0x0A:
                if self._discarding_line:
                    print("WARNING: discarded one oversized ESP32 serial line", flush=True)
                    self._discarding_line = False
                    self._buffer.clear()
                    continue
                line = self._buffer.rstrip(b"\r").decode("ascii", errors="ignore").strip()
                self._buffer.clear()
                if line.startswith("SWITCH:"):
                    events.append(line)
                continue
            if self._discarding_line:
                continue
            if len(self._buffer) >= MAX_ESP32_SERIAL_LINE_BYTES:
                self._buffer.clear()
                self._discarding_line = True
                continue
            self._buffer.append(byte)
        return events

    @property
    def connected(self) -> bool:
        return self.stream is not None

    def _mark_disconnected(self):
        self.close()
        self.connection_lost = True
        self.next_open_attempt = time.monotonic() + self.RECONNECT_INTERVAL_SECONDS

    def close(self):
        with self._lock:
            if self.stream:
                try:
                    self.stream.close()
                except OSError:
                    pass
                self.stream = None
            self._buffer.clear()
            self._discarding_line = False


class SwitchConversationGate:
    """Start on either physical edge while idle; ignore edges while active."""

    EDGE_EVENTS = {"SWITCH:EDGE:HIGH_TO_LOW", "SWITCH:EDGE:LOW_TO_HIGH"}

    def __init__(self):
        self.initialized = False
        self.switch_level: str | None = None
        self.active = False

    def handle(self, event: str, display_state: str = "IDLE") -> str | None:
        if event.startswith("SWITCH:STATE:"):
            level = event.rsplit(":", 1)[-1]
            self.switch_level = level if level in {"HIGH", "LOW"} else {
                "ON": "LOW", "OFF": "HIGH",
            }.get(level)
            self.initialized = True
            # Synchronization reports a level, never a physical transition.
            return "synced"

        # Accept prior edge-only firmware messages for backward compatibility.
        edge = event in self.EDGE_EVENTS or event in {"SWITCH:ON", "SWITCH:OFF"}
        if not edge:
            return None
        if self.active or display_state != "IDLE":
            return "ignored_active"
        self.active = True
        return "start"

    def cancel_active(self) -> str | None:
        """Cancel only for a lost serial link, not for a switch edge."""
        return "cancel" if self.active else None

    def finish(self, _outcome: str) -> None:
        self.active = False


class SpeechActivityDetector:
    """Small-window VAD with confirmed speech onset and post-speech silence."""

    def __init__(self, started_at: float, onset_timeout: float = LISTEN_SILENCE_TIMEOUT_SECONDS):
        self.started_at = started_at
        self.onset_timeout = onset_timeout
        self.started = False
        self.speech_started_at: float | None = None
        self.last_speech_at: float | None = None
        # Count audio samples, not wall-clock time. The Pi log showed 2 s
        # wall-clock waits but only 0.12 s of PCM reaching transcription.
        self.post_onset_audio_seconds = 0.0
        self._onset_votes = deque(maxlen=3)
        self.max_rms_dbfs = -120.0
        self.max_peak_dbfs = -120.0

    @staticmethod
    def levels(frame: bytes) -> tuple[float, float]:
        if len(frame) < 2 or len(frame) % 2:
            return -120.0, -120.0
        values = [sample[0] for sample in struct.iter_unpack("<h", frame)]
        if not values:
            return -120.0, -120.0
        peak = max(abs(value) for value in values)
        rms = math.sqrt(sum(value * value for value in values) / len(values))
        to_dbfs = lambda value: 20 * math.log10(max(value, 1) / 32767)
        return to_dbfs(rms), to_dbfs(peak)

    @classmethod
    def onset_frame(cls, frame: bytes) -> bool:
        rms, peak = cls.levels(frame)
        # The prior gate was stricter than the recording-level validator
        # (-55/-42 dBFS), rejecting quiet speech before saving/transcription.
        return rms >= -52.0 and peak >= -42.0

    @classmethod
    def continuation_frame(cls, frame: bytes) -> bool:
        rms, peak = cls.levels(frame)
        return rms >= -55.0 and peak >= -42.0

    def observe(self, frame: bytes, now: float) -> str | None:
        rms, peak = self.levels(frame)
        self.max_rms_dbfs = max(self.max_rms_dbfs, rms)
        self.max_peak_dbfs = max(self.max_peak_dbfs, peak)
        if not self.started:
            if onset_deadline_expired(self.started_at, now, self.onset_timeout):
                return "timeout"
            self._onset_votes.append(self.onset_frame(frame))
            if sum(self._onset_votes) >= 2:
                self.started = True
                self.speech_started_at = self.last_speech_at = now
                self.post_onset_audio_seconds += len(frame) / PCM_BYTES_PER_SECOND
                return "started"
            return None
        self.post_onset_audio_seconds += len(frame) / PCM_BYTES_PER_SECOND
        if self.continuation_frame(frame):
            self.last_speech_at = now
            return "speech"
        # A brief transient can satisfy the onset detector. Keep the stream
        # open long enough for the rest of an ordinary spoken question to
        # arrive before honoring the trailing-silence endpoint.
        if self.post_onset_audio_seconds < MIN_UTTERANCE_CAPTURE_AFTER_ONSET_SECONDS:
            return None
        if self.last_speech_at is not None and now - self.last_speech_at >= UTTERANCE_END_SILENCE_SECONDS:
            return "ended"
        return None


def require(condition: bool, message: str):
    if not condition:
        raise RuntimeError(message)


def _is_cp210x_tty(sys_entry: Path) -> bool:
    """Identify the ESP32 USB-UART bridge by its USB VID/PID, not tty number."""
    try:
        current = (sys_entry / "device").resolve(strict=True)
    except OSError:
        return False
    for _ in range(10):
        try:
            vendor = (current / "idVendor").read_text(encoding="ascii").strip().lower()
            product = (current / "idProduct").read_text(encoding="ascii").strip().lower()
            if (vendor, product) == ("10c4", "ea60"):
                return True
        except OSError:
            pass
        if current.parent == current:
            break
        current = current.parent
    return False


def discover_usb_uart_port(
    sys_class_tty: Path = Path("/sys/class/tty"),
    by_id_dir: Path = Path("/dev/serial/by-id"),
) -> str:
    """Return the stable by-id path for the CP210x if present, else ttyUSB0.

    LEO_UART_PORT remains an explicit override. If it is unset, this lookup is
    repeated on reconnect so USB re-enumeration to ttyUSB1 is handled safely.
    """
    cp210x_names = {
        entry.name for entry in sys_class_tty.glob("ttyUSB*")
        if _is_cp210x_tty(entry)
    }
    if cp210x_names:
        for candidate in sorted(by_id_dir.glob("*")):
            try:
                if candidate.resolve(strict=True).name in cp210x_names:
                    return str(candidate)
            except OSError:
                continue
        return f"/dev/{sorted(cp210x_names)[0]}"
    return DEFAULT_UART_PORT


def run(cmd: list[str], description: str):
    print(f"==> {description}", flush=True)
    result = subprocess.run(cmd, cwd=PROJECT_DIR, check=True)
    print(f"Subprocess exit ({description}): {result.returncode}", flush=True)


def run_cancellable(
    cmd: list[str],
    description: str,
    cancel_event: threading.Event | None = None,
    on_started=None,
) -> bool:
    """Run a child process while allowing switch-OFF to stop playback/capture."""
    print(f"==> {description}", flush=True)
    process = subprocess.Popen(cmd, cwd=PROJECT_DIR)
    if on_started:
        on_started()
    while True:
        if cancel_event is None:
            return_code = process.wait()
            break
        try:
            return_code = process.wait(timeout=0.05)
            break
        except subprocess.TimeoutExpired:
            if cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                print(f"Cancelled {description}", flush=True)
                return False
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, cmd)
    print(f"Subprocess exit ({description}): {return_code}", flush=True)
    return True


def timed(label: str, operation):
    started = time.monotonic()
    try:
        return operation()
    finally:
        print(f"TIMING {label}: {time.monotonic() - started:.2f}s", flush=True)


def ensure_tools():
    missing = [tool for tool in ("arecord", "ffmpeg", "ffprobe", "mpg123") if not shutil.which(tool)]
    require(not missing, f"Missing required tool(s): {', '.join(missing)}")
    require(RECORD_SECONDS > 0, "LEO_RECORD_SECONDS must be greater than zero")
    require(-24 <= AUDIO_TARGET_LUFS <= -12, "LEO_AUDIO_TARGET_LUFS must be between -24 and -12 LUFS")
    require(-9 <= AUDIO_TRUE_PEAK_DBTP <= -0.1, "LEO_AUDIO_TRUE_PEAK_DBTP must be between -9 and -0.1 dBTP")
    require(1 <= TTS_TIMEOUT_SECONDS <= 60, "LEO_TTS_TIMEOUT_SECONDS must be between 1 and 60")


def report_microphone_configuration():
    """Log the configured ALSA input and the Pi's enumerated capture hardware."""
    print(
        f"Microphone input: ALSA device={MIC_DEVICE!r}, format=S16_LE, 16000 Hz, mono; "
        "override with LEO_MIC_DEVICE if this is not the intended microphone.",
        flush=True,
    )
    try:
        result = subprocess.run(
            ["arecord", "-l"], capture_output=True, text=True, check=False, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"WARNING: unable to enumerate ALSA microphones ({type(exc).__name__}: {exc})", flush=True)
        return
    inventory = result.stdout.strip()
    if inventory:
        print("ALSA capture-device inventory (arecord -l):\n" + inventory, flush=True)
    else:
        details = result.stderr.strip() or "arecord reported no capture cards"
        print(f"WARNING: no ALSA capture device was listed: {details}", flush=True)


def make_speech(client: OpenAI, text: str, output: Path, instructions: str = TTS_INSTRUCTIONS):
    """Request MP3 speech and separately time response start and stream/file writing."""
    output.unlink(missing_ok=True)
    tts_client = client.with_options(timeout=TTS_TIMEOUT_SECONDS, max_retries=0)
    request_started = time.monotonic()
    try:
        with tts_client.audio.speech.with_streaming_response.create(
            model=TTS_MODEL, voice=TTS_VOICE, input=text,
            instructions=instructions, response_format="mp3"
        ) as response:
            print(f"TIMING TTS response start: {time.monotonic() - request_started:.2f}s", flush=True)
            stream_started = time.monotonic()
            response.stream_to_file(output)
            print(f"TIMING TTS stream download/file write: {time.monotonic() - stream_started:.2f}s", flush=True)
    except (OpenAIError, OSError) as exc:
        raise RuntimeError(f"TTS API or audio stream failed: {exc}") from exc


def prompt_cache_fingerprint(text: str, instructions: str = TTS_INSTRUCTIONS) -> str:
    cache_identity = json.dumps(
        {"version": 1, "model": TTS_MODEL, "voice": TTS_VOICE,
         "instructions": instructions, "text": text, "format": "mp3"},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(cache_identity.encode("utf-8")).hexdigest()


def prompt_cache_metadata_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".json")


def prompt_cache_is_current(output: Path, text: str, instructions: str = TTS_INSTRUCTIONS) -> bool:
    metadata_path = prompt_cache_metadata_path(output)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return output.is_file() and output.stat().st_size > 1024 and metadata == {
            "fingerprint": prompt_cache_fingerprint(text, instructions)
        }
    except (OSError, json.JSONDecodeError):
        return False


def write_prompt_cache_metadata(output: Path, text: str, instructions: str = TTS_INSTRUCTIONS):
    metadata_path = prompt_cache_metadata_path(output)
    temporary = metadata_path.with_name(f".{metadata_path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps({"fingerprint": prompt_cache_fingerprint(text, instructions)}) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, metadata_path)


def validate_speech_file(path: Path):
    """Check that the TTS result decodes and contains non-silent audio."""
    require(path.is_file() and path.stat().st_size > 1024, "TTS produced a missing or undersized audio file")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,sample_rate,channels",
         "-of", "json", str(path)], check=True, capture_output=True, text=True,
    )
    info = json.loads(probe.stdout)
    streams = info.get("streams", [])
    duration = float(info.get("format", {}).get("duration", 0))
    require(streams and duration > 0.25, "TTS audio is invalid or truncated")
    levels = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    )
    report = levels.stderr
    mean_match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", report)
    peak_match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", report)
    require(mean_match and peak_match, "Could not measure TTS audio level")
    mean_dbfs, peak_dbfs = float(mean_match.group(1)), float(peak_match.group(1))
    require(mean_dbfs > -50 and peak_dbfs > -35, "TTS output appears silent or unusably quiet")
    print(
        f"Audio validated: {path.name}, {duration:.2f}s, "
        f"{streams[0].get('codec_name')} {streams[0].get('sample_rate')}Hz "
        f"{streams[0].get('channels')}ch, mean {mean_dbfs:.1f} dBFS, peak {peak_dbfs:.1f} dBFS",
        flush=True,
    )


def play_mp3(
    path: Path,
    label: str,
    normalize_loudness: bool = False,
    ready_since: float | None = None,
    cancel_event: threading.Event | None = None,
    on_started=None,
):
    """Apply the common loudness/peak treatment before any voice stage is played."""
    if cancel_event and cancel_event.is_set():
        return False
    if normalize_loudness:
        REPLY_PLAYBACK_MP3.unlink(missing_ok=True)
        def normalize():
            command = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(path), "-af",
                       f"loudnorm=I={AUDIO_TARGET_LUFS}:TP={AUDIO_TRUE_PEAK_DBTP:g}:LRA=11", str(REPLY_PLAYBACK_MP3)]
            if cancel_event:
                return run_cancellable(command, f"Normalizing {label} loudness", cancel_event)
            run(command, f"Normalizing {label} loudness")
            return True
        if not timed(f"audio loudness processing ({label})", normalize):
            return False
        path = REPLY_PLAYBACK_MP3
    require(path.is_file() and path.stat().st_size > 0, f"Playback file is missing: {path}")
    if normalize_loudness:
        timed("processed audio validation", lambda: validate_speech_file(path))
    if cancel_event and cancel_event.is_set():
        return False
    print(f"Playback input: {path}; output: ALSA default device", flush=True)
    if ready_since is not None:
        print(f"TIMING total until reply ready: {time.monotonic() - ready_since:.2f}s", flush=True)
    def playback():
        command = ["mpg123", "-o", "alsa", "-q", str(path)]
        return run_cancellable(command, f"Playing {label}", cancel_event, on_started)
    return timed(f"playback ({label})", playback)


def play_prompt(
    client: OpenAI,
    text: str,
    output: Path,
    label: str,
    instructions: str = TTS_INSTRUCTIONS,
    cancel_event: threading.Event | None = None,
):
    if cancel_event and cancel_event.is_set():
        return False
    if not prompt_cache_is_current(output, text, instructions):
        print(f"==> Creating/updating cached {label} prompt ({TTS_VOICE})", flush=True)
        with tempfile.NamedTemporaryFile(prefix=f".{output.stem}.", suffix=".mp3", dir=output.parent, delete=False) as handle:
            temporary_output = Path(handle.name)
        try:
            timed(f"TTS ({label} prompt)", lambda: make_speech(client, text, temporary_output, instructions))
            if cancel_event and cancel_event.is_set():
                return False
            timed(f"TTS ({label} prompt) validation", lambda: validate_speech_file(temporary_output))
            os.replace(temporary_output, output)
            write_prompt_cache_metadata(output, text, instructions)
        finally:
            temporary_output.unlink(missing_ok=True)
    if cancel_event:
        return play_mp3(output, f"{label} prompt", normalize_loudness=True, cancel_event=cancel_event)
    return play_mp3(output, f"{label} prompt", normalize_loudness=True)


def wav_metrics(path: Path) -> tuple[float, float]:
    """Return RMS and peak dBFS for 16-bit WAV data without extra dependencies."""
    with wave.open(str(path), "rb") as source:
        require(source.getsampwidth() == 2, "Recording must be 16-bit PCM WAV")
        frames = source.readframes(source.getnframes())
    samples = memoryview(frames).cast("h")
    require(len(samples) > 0, "Recording contains no audio frames")
    peak = max(abs(sample) for sample in samples)
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    to_dbfs = lambda value: 20 * math.log10(max(value, 1) / 32767)
    return to_dbfs(rms), to_dbfs(peak)


def contains_meaningful_audio(rms_dbfs: float, peak_dbfs: float) -> bool:
    return rms_dbfs > -55 and peak_dbfs > -42


def usable_utterance_duration(duration_seconds: float) -> bool:
    """Reject tiny VAD triggers (clicks/brief bursts) before calling ASR."""
    return duration_seconds >= MIN_UTTERANCE_AUDIO_SECONDS


def normalize_recording(
    source: Path,
    destination: Path,
    cancel_event: threading.Event | None = None,
):
    rms_dbfs, peak_dbfs = wav_metrics(source)
    print(f"Recording level: RMS {rms_dbfs:.1f} dBFS, peak {peak_dbfs:.1f} dBFS", flush=True)
    require(contains_meaningful_audio(rms_dbfs, peak_dbfs), "No speech-level audio was detected")
    # Set a safe peak instead of applying the former fixed +18 dB boost to every capture.
    gain_db = max(0.0, min(18.0, -3.0 - peak_dbfs))
    print(f"Microphone normalization gain: {gain_db:.1f} dB", flush=True)
    destination.unlink(missing_ok=True)
    command = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(source), "-af",
               f"volume={gain_db:.2f}dB,alimiter=limit=0.95", str(destination)]
    if cancel_event:
        if not run_cancellable(command, "Normalizing microphone recording", cancel_event):
            return False
    else:
        run(command, "Normalizing microphone recording")
    return True


def record_audio():
    for path in (INPUT_WAV, BOOSTED_WAV):
        path.unlink(missing_ok=True)
    require(bool(MIC_DEVICE.strip()), "LEO_MIC_DEVICE is empty; configure a valid ALSA capture device")
    timed("recording", lambda: run(record_command(), f"Recording {RECORD_SECONDS} seconds from {MIC_DEVICE}"))
    require(INPUT_WAV.exists(), "Recorder did not create input.wav")
    timed("microphone normalization", lambda: normalize_recording(INPUT_WAV, BOOSTED_WAV))
    BOOSTED_WAV.replace(INPUT_WAV)


def record_command(
    output: Path = INPUT_WAV,
    duration_seconds: int | None = None,
) -> list[str]:
    """Build the validated ALSA capture command from the effective device setting."""
    require(bool(MIC_DEVICE.strip()), "LEO_MIC_DEVICE is empty; configure a valid ALSA capture device")
    duration = RECORD_SECONDS if duration_seconds is None else max(1, int(duration_seconds))
    return ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", "16000", "-c", "1",
            "-d", str(duration), str(output)]


def inspect_wav_onset(path: Path, onset_timeout: float) -> SpeechActivityDetector:
    """Run the same 20 ms onset detector on a completed 16 kHz mono WAV."""
    detector = SpeechActivityDetector(started_at=0.0, onset_timeout=onset_timeout)
    with wave.open(str(path), "rb") as recording:
        require((recording.getnchannels(), recording.getsampwidth(), recording.getframerate())
                == (1, 2, 16000), "Capture WAV format does not match 16 kHz mono S16_LE")
        frame_number = 0
        while True:
            frame = recording.readframes(320)  # 20 ms at 16 kHz
            if len(frame) < 640:
                break
            frame_number += 1
            detector.observe(frame, frame_number * 0.02)
            if detector.started:
                break
    return detector


def record_utterance(
    cancel_event: threading.Event,
    onset_timeout: float = LISTEN_SILENCE_TIMEOUT_SECONDS,
) -> bool | None:
    """Record a complete WAV window, then detect onset offline; None means no onset."""
    require(bool(MIC_DEVICE.strip()), "LEO_MIC_DEVICE is empty; configure a valid ALSA capture device")
    for path in (INPUT_WAV, BOOSTED_WAV):
        path.unlink(missing_ok=True)
    require(onset_timeout > 0, "No time remains in the speech-onset window")
    capture_seconds = min(
        int(math.ceil(onset_timeout)),
        int(math.ceil(LISTEN_SILENCE_TIMEOUT_SECONDS)),
    )
    command = record_command(INPUT_WAV, capture_seconds)
    print(
        f"==> Recording up to {capture_seconds}s from {MIC_DEVICE} "
        f"({max(0.0, onset_timeout):.1f}s onset window; S16_LE, 16 kHz, mono)",
        flush=True,
    )
    if not run_cancellable(command, f"Recording {capture_seconds}s from {MIC_DEVICE}", cancel_event):
        return False
    if cancel_event.is_set():
        return False
    require(INPUT_WAV.is_file() and INPUT_WAV.stat().st_size > 44,
            "Capture did not produce a non-empty WAV file")
    with wave.open(str(INPUT_WAV), "rb") as recording:
        channels = recording.getnchannels()
        sample_width = recording.getsampwidth()
        sample_rate = recording.getframerate()
        frame_count = recording.getnframes()
    require((channels, sample_width, sample_rate) == (1, 2, 16000) and frame_count > 0,
            "Capture WAV format does not match 16 kHz mono S16_LE")
    duration_seconds = frame_count / sample_rate
    detector = inspect_wav_onset(INPUT_WAV, onset_timeout)
    rms_dbfs, peak_dbfs = wav_metrics(INPUT_WAV)
    print(
        f"Recording saved: {INPUT_WAV.name}, {INPUT_WAV.stat().st_size} bytes, "
        f"{channels} channel, {sample_rate} Hz, {sample_width * 8}-bit, "
        f"{duration_seconds:.2f}s; RMS {rms_dbfs:.1f} dBFS, peak {peak_dbfs:.1f} dBFS",
        flush=True,
    )
    if not detector.started:
        print(
            "No speech onset detected within the listening window "
            f"(peak frame RMS={detector.max_rms_dbfs:.1f} dBFS, "
            f"peak={detector.max_peak_dbfs:.1f} dBFS)",
            flush=True,
        )
        return None
    print("Speech onset detected in completed capture; transcribing full WAV", flush=True)
    if not usable_utterance_duration(duration_seconds):
        message = (
            f"Recording too short ({duration_seconds:.2f}s; minimum "
            f"{MIN_UTTERANCE_AUDIO_SECONDS:.2f}s); not sending to transcription"
        )
        print(message, flush=True)
        raise RuntimeError(message)
    if not normalize_recording(INPUT_WAV, BOOSTED_WAV, cancel_event):
        return False
    if cancel_event.is_set():
        return False
    BOOSTED_WAV.replace(INPUT_WAV)
    return True


def local_time_reply(transcript: str, now: datetime | None = None) -> str | None:
    """Answer only unambiguous local-clock questions without an API request."""
    question = re.sub(r"[^a-z0-9' ]+", " ", transcript.lower()).strip()
    asks_time = bool(re.search(r"\bwhat(?:'s| is)? (?:the )?(?:current )?time\b|\bwhat time is it\b|\btell me (?:the )?time\b|\bcurrent time\b", question))
    asks_date = bool(re.search(r"\b(today'?s )?date\b|\bwhat date is it\b", question))
    asks_day = bool(re.search(r"\bwhat day is it\b|\bday is (it )?today\b", question))
    if not (asks_time or asks_date or asks_day):
        return None
    local_now = now or datetime.now().astimezone()
    if asks_time and not (asks_date or asks_day):
        return f"It is {local_now.strftime('%I').lstrip('0')}:{local_now:%M %p} {local_now.tzname()}."
    return f"Today is {local_now:%A, %B} {local_now.day}, {local_now.year}."


def response_is_english(text: str) -> bool:
    """Reject responses containing letters from non-Latin writing systems."""
    return not any(char.isalpha() and not ("LATIN" in unicodedata.name(char, "")) for char in text)


def generate_reply(client: OpenAI, transcript: str, history: list[dict[str, str]], reminder_store=None) -> str:
    reminder_handled = False
    try:
        reply = handle_reminder_command(transcript, reminder_store)
        reminder_handled = reply is not None
    except sqlite3.Error as exc:
        print(f"Reminder database unavailable ({type(exc).__name__})", flush=True)
        reply = "I couldn't access the reminder list just now. Your voice assistant is still available."
        reminder_handled = True
    if reminder_handled:
        print("Assistant reply source: local reminder database", flush=True)
    else:
        reply = local_time_reply(transcript)
        if reply:
            print("Assistant reply source: local system clock", flush=True)
    if not reply:
        messages = history + [{"role": "user", "content": transcript}]
        try:
            reply = timed("text response API", lambda: client.responses.create(
                model=TEXT_MODEL,
                instructions=f"{FRIENDLY_BASE_PROMPT} {ENGLISH_INSTRUCTIONS}",
                input=messages,
                max_output_tokens=160,
            ).output_text.strip())
        except OpenAIError as exc:
            raise RuntimeError(f"Text response API failed: {exc}") from exc
    require(reply, "AI model returned no text")
    if not response_is_english(reply):
        print("Response language check failed for English; using a clear fallback", flush=True)
        return NON_ENGLISH_FALLBACK
    return reply


def load_conversation(path: Path = CONVERSATION_FILE) -> list[dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list) and all(item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str) for item in data):
            return data[-CONVERSATION_TURNS * 2:]
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return []


def save_conversation(messages: list[dict[str, str]], path: Path = CONVERSATION_FILE):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(messages[-CONVERSATION_TURNS * 2:], ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def transcribe(client: OpenAI) -> str:
    print(f"Transcription started: model={TRANSCRIBE_MODEL}, input={INPUT_WAV.name}", flush=True)
    def request():
        with INPUT_WAV.open("rb") as audio:
            return client.audio.transcriptions.create(
                model=TRANSCRIBE_MODEL,
                file=audio,
                extra_body={"languages": ["en"]},
            )
    try:
        result = timed("transcription API", request)
    except OpenAIError as exc:
        raise RuntimeError(f"Transcription API failed: {exc}") from exc
    transcript = result.text.strip()
    if not transcript:
        print("Transcription result: empty; no answer will be generated", flush=True)
        raise RuntimeError("Speech recognition returned no text")
    print(f"Transcription result: non-empty ({len(transcript)} characters)", flush=True)
    return transcript


def speak_reply(
    client: OpenAI,
    uart: UartStateSender,
    reply: str,
    interaction_started: float | None = None,
    cancel_event: threading.Event | None = None,
):
    if cancel_event and cancel_event.is_set():
        return False
    REPLY_MP3.unlink(missing_ok=True)
    print(f"TTS configuration: model={TTS_MODEL}, voice={TTS_VOICE}, format=mp3, timeout={TTS_TIMEOUT_SECONDS:g}s, retries=0", flush=True)
    timed("TTS request/stream", lambda: make_speech(client, reply, REPLY_MP3))
    if cancel_event and cancel_event.is_set():
        return False
    timed("TTS audio validation", lambda: validate_speech_file(REPLY_MP3))
    if cancel_event:
        return play_mp3(
            REPLY_MP3, "assistant response", normalize_loudness=True,
            ready_since=interaction_started, cancel_event=cancel_event,
            on_started=lambda: uart.send("SPEAKING"),
        )
    return play_mp3(
        REPLY_MP3, "assistant response", normalize_loudness=True,
        ready_since=interaction_started, on_started=lambda: uart.send("SPEAKING"),
    )


def run_conversation_session(
    client: OpenAI,
    uart: UartStateSender,
    reminder_store: ReminderStore | None,
    reminder_scheduler: ReminderScheduler | None,
    cancel_event: threading.Event,
) -> str:
    """Run greeting plus repeated listen/think/speak turns until OFF or timeout."""
    history = load_conversation()
    uart.send("LISTENING")
    if not play_prompt(
        client, LISTENING_PROMPT_TEXT, LISTENING_PROMPT_MP3, "listening",
        LISTENING_TTS_INSTRUCTIONS, cancel_event,
    ):
        return "cancelled" if cancel_event.is_set() else "error"

    # One deadline belongs to each LISTENING period. Failed captures and empty
    # transcriptions must not restart it; only a completed answer starts a new
    # listening period below.
    listen_deadline: float | None = time.monotonic() + LISTEN_SILENCE_TIMEOUT_SECONDS
    while not cancel_event.is_set():
        if listen_deadline is None:
            uart.send("LISTENING")
            listen_deadline = time.monotonic() + LISTEN_SILENCE_TIMEOUT_SECONDS
        capture_started = time.monotonic()
        remaining_onset = max(0.0, listen_deadline - capture_started)
        if remaining_onset <= 0:
            return "silence_timeout"
        try:
            captured = timed(
                "speech capture",
                lambda: record_utterance(cancel_event, onset_timeout=remaining_onset),
            )
        except RuntimeError as exc:
            if (str(exc) != "No speech-level audio was detected"
                    and not str(exc).startswith((
                        "Speech segment too short (",
                        "Microphone capture stalled after speech onset",
                    ))):
                raise
            if cancel_event.is_set():
                return "cancelled"
            print(f"Capture not usable; remaining in LISTENING: {exc}", flush=True)
            # Keep the original listening deadline; retries must not keep the
            # session alive forever on noise or repeated capture failures.
            continue
        if captured is None:
            return "silence_timeout"
        if not captured or cancel_event.is_set():
            return "cancelled"

        try:
            transcript = transcribe(client)
        except RuntimeError as exc:
            message = str(exc)
            retryable = message in {
                "No speech-level audio was detected", "Speech recognition returned no text",
            } or message.startswith((
                "Transcription API failed:", "Speech segment too short (",
                "Microphone capture stalled after speech onset",
            ))
            if not retryable:
                raise
            if cancel_event.is_set():
                return "cancelled"
            print(f"Transcription/capture did not yield a usable question; remaining in LISTENING: {exc}", flush=True)
            # Do not refresh the deadline after an empty transcript. This
            # prevents false/noisy onset detections from holding the session
            # open forever. A valid answer creates the next listening window.
            continue
        if cancel_event.is_set():
            return "cancelled"

        print(f"Transcript accepted for processing ({len(transcript)} characters)", flush=True)
        # Only a non-empty transcription advances the state or plays the
        # thinking prompt. The capture window remains LISTENING while ASR runs.
        uart.send("THINKING")
        if not play_prompt(client, THINKING_PROMPT_TEXT, THINKING_PROMPT_MP3, "thinking", cancel_event=cancel_event):
            return "cancelled" if cancel_event.is_set() else "error"
        if cancel_event.is_set():
            return "cancelled"
        reply = generate_reply(client, transcript, history, reminder_store)
        if cancel_event.is_set():
            return "cancelled"
        print(f"Assistant reply (English): {reply}", flush=True)
        if not speak_reply(client, uart, reply, interaction_started=capture_started, cancel_event=cancel_event):
            return "cancelled" if cancel_event.is_set() else "error"
        if cancel_event.is_set():
            return "cancelled"
        history.extend([{"role": "user", "content": transcript}, {"role": "assistant", "content": reply}])
        save_conversation(history)
        history = history[-CONVERSATION_TURNS * 2:]
        if reminder_scheduler:
            for reminder in reminder_scheduler.claim_due():
                if cancel_event.is_set():
                    return "cancelled"
                if not speak_reply(
                    client, uart, f"Reminder: {reminder.title}.", cancel_event=cancel_event,
                ):
                    return "cancelled" if cancel_event.is_set() else "error"
        print(f"TIMING voice turn total: {time.monotonic() - capture_started:.2f}s", flush=True)
        listen_deadline = None

    return "cancelled"


def _session_worker(client, uart, reminder_store, reminder_scheduler, cancel_event, results: Queue):
    try:
        results.put((run_conversation_session(
            client, uart, reminder_store, reminder_scheduler, cancel_event,
        ), None))
    except Exception as exc:
        results.put(("error", exc))


def main():
    require(os.environ.get("OPENAI_API_KEY"), "OPENAI_API_KEY is not set")
    print(f"Leo Buddy voice app build: {VOICE_APP_BUILD}; TTS voice={TTS_VOICE}", flush=True)
    ensure_tools()
    report_microphone_configuration()
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=35.0, max_retries=0)
    uart = UartStateSender(UART_PORT)
    reminder_store = None
    reminder_scheduler = None
    try:
        reminder_store = ReminderStore()
        reminder_scheduler = ReminderScheduler(reminder_store)
        reminder_scheduler.start()
    except (OSError, sqlite3.Error) as exc:
        print(f"WARNING: reminders unavailable ({type(exc).__name__}); continuing with voice only", flush=True)
    started = time.monotonic()
    try:
        uart.open()
        print(f"ESP32 USB serial connected on {uart.port} at 115200 baud", flush=True)
    except OSError as exc:
        print(f"WARNING: status UART unavailable ({exc}); continuing without display updates", flush=True)
    uart.send("IDLE")
    uart.send("SYNC")
    gate = SwitchConversationGate()
    worker = None
    cancel_event = None
    results = None
    error_state_until = 0.0
    def launch_session():
        nonlocal worker, cancel_event, results
        cancel_event = threading.Event()
        results = Queue()
        worker = threading.Thread(
            target=_session_worker,
            args=(client, uart, reminder_store, reminder_scheduler, cancel_event, results),
            name="leo-voice-session", daemon=True,
        )
        worker.start()
        print("GPIO23 edge: starting a new voice session", flush=True)

    try:
        print("Leo Buddy ready in IDLE; either debounced GPIO23 edge starts a conversation.", flush=True)
        while True:
            if error_state_until and time.monotonic() >= error_state_until:
                uart.send("IDLE")
                error_state_until = 0.0
            events = uart.poll_events()
            if uart.connection_lost:
                uart.connection_lost = False
                if gate.cancel_active():
                    if cancel_event:
                        cancel_event.set()
                    print("ESP32 serial disconnected; cancelling active session safely", flush=True)
            for event in events:
                print(f"ESP32 switch event received: {event}", flush=True)
                action = gate.handle(event, uart.display_state)
                if event.startswith("SWITCH:STATE:"):
                    uart.send(uart.display_state)
                    print(f"ESP32 switch level synchronized: {event.rsplit(':', 1)[-1]}", flush=True)
                elif action == "start":
                    if worker is None:
                        print("GPIO23 edge accepted while Pi state is IDLE", flush=True)
                        launch_session()
                    else:
                        print("Switch edge received while a previous session is stopping; ignored", flush=True)
                elif action == "cancel":
                    if cancel_event:
                        cancel_event.set()
                    uart.send("IDLE")
                    print("Cancelling active session after USB serial disconnect", flush=True)
                elif action == "ignored_active":
                    print("GPIO23 transition ignored while conversation is active", flush=True)

            if worker is not None and not worker.is_alive():
                outcome, error = results.get_nowait()
                worker = None
                if error is not None:
                    print(f"Voice session failed ({type(error).__name__}): {error}", flush=True)
                    gate.finish("error")
                    uart.send("ERROR")
                    error_state_until = time.monotonic() + 1.0
                else:
                    print(f"Voice session ended: {outcome}", flush=True)
                    gate.finish(outcome)
                    uart.send("IDLE")
                cancel_event = None
                results = None
            time.sleep(0.03)
    except KeyboardInterrupt:
        print("Shutdown requested", flush=True)
    finally:
        if cancel_event:
            cancel_event.set()
        if worker:
            worker.join(timeout=2.0)
        if reminder_scheduler:
            if worker is None or not worker.is_alive():
                due_reminders = reminder_scheduler.stop_and_claim_due()
            else:
                reminder_scheduler._stop.set()
                reminder_scheduler._thread.join(timeout=1.0)
                due_reminders = []
            for reminder in due_reminders:
                try:
                    speak_reply(client, uart, f"Reminder: {reminder.title}.")
                except (RuntimeError, OSError, subprocess.CalledProcessError, OpenAIError) as exc:
                    print(f"WARNING: reminder {reminder.id} could not be announced ({type(exc).__name__})", flush=True)
        print(f"TIMING application runtime: {time.monotonic() - started:.2f}s", flush=True)
        uart.send("IDLE")
        uart.close()


if __name__ == "__main__":
    try: main()
    except (RuntimeError, subprocess.CalledProcessError, OSError, OpenAIError, wave.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True); sys.exit(1)
