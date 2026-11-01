import contextlib
import io
import os
import pty
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openai import OpenAIError

import voice_demo as app


class EnglishPipelineTests(unittest.TestCase):
    def test_uart_defaults_to_usb_and_writes_newline_delimited_state(self):
        self.assertEqual(app.DEFAULT_UART_PORT, "/dev/ttyUSB0")
        master_fd, slave_fd = pty.openpty()
        sender = app.UartStateSender(os.ttyname(slave_fd))
        try:
            self.assertTrue(sender.open())
            self.assertTrue(sender.send("LISTENING"))
            self.assertEqual(os.read(master_fd, 32), b"LISTENING\n")
            self.assertTrue(sender.send("ERROR"))
            self.assertEqual(os.read(master_fd, 32), b"ERROR\n")
        finally:
            sender.close()
            os.close(master_fd)
            os.close(slave_fd)

    def test_uart_disconnect_is_nonfatal_and_retries_are_throttled(self):
        sender = app.UartStateSender("/tmp/leo-missing-serial-device")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(sender.send("THINKING"))
            retry_deadline = sender.next_open_attempt
            self.assertFalse(sender.send("SPEAKING"))
        self.assertGreater(retry_deadline, 0)
        self.assertIsNone(sender.stream)

    def test_cp210x_usb_uart_discovery_uses_stable_by_id_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sys_root = root / "sys/class/tty"
            sys_root.mkdir(parents=True)
            usb_device = root / "sys/devices/usb/1-1/1-1:1.0"
            usb_device.mkdir(parents=True)
            (usb_device / "idVendor").write_text("10c4\n", encoding="ascii")
            (usb_device / "idProduct").write_text("ea60\n", encoding="ascii")
            tty_device = usb_device / "ttyUSB2"
            tty_device.mkdir()
            tty_entry = sys_root / "ttyUSB2"
            tty_entry.mkdir()
            (tty_entry / "device").symlink_to(tty_device)
            by_id = root / "dev/serial/by-id"
            by_id.mkdir(parents=True)
            target = root / "dev/ttyUSB2"
            target.touch()
            stable_link = by_id / "usb-Silicon_Labs_CP210x_USB_to_UART_Bridge-if00-port0"
            stable_link.symlink_to(target)
            self.assertEqual(
                app.discover_usb_uart_port(sys_root, by_id), str(stable_link)
            )
        self.assertEqual(app.discover_usb_uart_port(Path("/no/such/sys"), Path("/no/by-id")), app.DEFAULT_UART_PORT)

    def test_english_models_devices_and_bounded_tts_policy(self):
        self.assertEqual(app.TEXT_MODEL, "gpt-4o-mini")
        self.assertEqual(app.TTS_MODEL, "gpt-4o-mini-tts")
        self.assertEqual(app.TTS_VOICE, "cedar")
        self.assertIn("warm, friendly, relaxed conversational tone", app.TTS_INSTRUCTIONS)
        self.assertIn("letting phrases flow smoothly together", app.TTS_INSTRUCTIONS)
        self.assertIn("Avoid dramatic pauses, word-by-word emphasis", app.TTS_INSTRUCTIONS)
        self.assertIn("smoothly connected speech", app.LISTENING_TTS_INSTRUCTIONS)
        self.assertEqual(app.LISTENING_PROMPT_TEXT, "Hey Rohan! What's up? I'm listening.")
        self.assertEqual(app.THINKING_PROMPT_TEXT, "Alright, let me think.")
        self.assertEqual(app.MIC_DEVICE, "plughw:CARD=Device,DEV=0")
        self.assertEqual(app.TTS_TIMEOUT_SECONDS, 25)
        self.assertEqual(app.AUDIO_TARGET_LUFS, -20)
        self.assertEqual(app.AUDIO_TRUE_PEAK_DBTP, -1.5)
        self.assertEqual(app.record_command()[0:3], ["arecord", "-D", "plughw:CARD=Device,DEV=0"])

    def test_transcription_is_english_hinted_and_empty_text_is_safe(self):
        class Transcriptions:
            def create(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(text="  ")

        api = Transcriptions()
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=api))
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"fake input for mocked API")
            with patch.object(app, "INPUT_WAV", audio):
                with self.assertRaisesRegex(RuntimeError, "Speech recognition returned no text"):
                    app.transcribe(client)
        self.assertEqual(api.kwargs["extra_body"]["languages"], ["en"])
        self.assertEqual(api.kwargs["model"], app.TRANSCRIBE_MODEL)

    def test_transcription_api_failure_has_stage_specific_error(self):
        class Transcriptions:
            def create(self, **kwargs):
                raise OpenAIError("mock failure")

        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=Transcriptions()))
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(b"fake input")
            with patch.object(app, "INPUT_WAV", audio):
                with self.assertRaisesRegex(RuntimeError, "Transcription API failed"):
                    app.transcribe(client)

    def test_silent_recording_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "silence.wav"
            with wave.open(str(audio), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 1600)
            rms, peak = app.wav_metrics(audio)
            self.assertFalse(app.contains_meaningful_audio(rms, peak))
            with self.assertRaisesRegex(RuntimeError, "No speech-level audio"):
                app.normalize_recording(audio, Path(tmp) / "normalized.wav")

    def test_english_clock_answers_and_non_english_script_guard(self):
        fixed = datetime(2026, 10, 13, 9, 5).astimezone()
        self.assertIn("9:05 AM", app.local_time_reply("What time is it in India?", fixed))
        self.assertIn("Tuesday, October 13, 2026", app.local_time_reply("What is today's date?", fixed))
        self.assertIn("Tuesday", app.local_time_reply("What day is it today?", fixed))
        self.assertIsNone(app.local_time_reply("Tell me a joke", fixed))
        self.assertTrue(app.response_is_english("A microcontroller is a tiny computer."))
        self.assertFalse(app.response_is_english("ഇത് ഒരു കമ്പ്യൂട്ടറാണ്."))
        self.assertFalse(app.response_is_english("यह एक कंप्यूटर है।"))

    def test_greeting_factual_technical_and_followup_use_english_context(self):
        utterances = (
            ("Hello, LEO!", [], "Hi! Good to hear from you."),
            ("What is the capital of Japan?", [], "Tokyo is the capital of Japan."),
            ("Explain a microcontroller simply.", [], "It is a tiny computer that controls an electronic device."),
            ("Can you make that simpler?", [{"role": "user", "content": "Explain a microcontroller simply."}, {"role": "assistant", "content": "It is a tiny computer that controls an electronic device."}], "Think of it as a small computer inside a gadget."),
        )

        class Responses:
            def create(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(output_text=utterances[len(self.calls) - 1][2])

        responses = Responses()
        responses.calls = []
        client = SimpleNamespace(responses=responses)
        for utterance, context, _ in utterances:
            with self.subTest(utterance=utterance):
                result = app.generate_reply(client, utterance, context)
                self.assertTrue(app.response_is_english(result))
        self.assertEqual(len(responses.calls), 4)
        for call, (utterance, context, _) in zip(responses.calls, utterances):
            self.assertEqual(call["model"], "gpt-4o-mini")
            self.assertIn("natural English", call["instructions"])
            self.assertEqual(call["input"], context + [{"role": "user", "content": utterance}])

    def test_local_time_avoids_api_and_non_english_model_output_uses_english_fallback(self):
        class Responses:
            def create(self, **kwargs):
                raise AssertionError("local time should not call the API")

        client = SimpleNamespace(responses=Responses())
        self.assertIsNotNone(app.generate_reply(client, "What time is it?", []))

        class HindiResponses:
            def create(self, **kwargs):
                return SimpleNamespace(output_text="यह एक उत्तर है।")

        reply = app.generate_reply(SimpleNamespace(responses=HindiResponses()), "Hello", [])
        self.assertEqual(reply, app.NON_ENGLISH_FALLBACK)

    def test_text_response_api_failure_is_stage_specific(self):
        class Responses:
            def create(self, **kwargs):
                raise OpenAIError("mock failure")

        with self.assertRaisesRegex(RuntimeError, "Text response API failed"):
            app.generate_reply(SimpleNamespace(responses=Responses()), "Tell me a fact.", [])

    def test_tts_api_failure_is_stage_specific_and_has_no_retry(self):
        request_options = {}
        request_args = {}

        class SpeechEndpoint:
            def create(self, **kwargs):
                request_args.update(kwargs)
                raise OpenAIError("mock failure")

        def with_options(**kwargs):
            request_options.update(kwargs)
            return SimpleNamespace(
                audio=SimpleNamespace(speech=SimpleNamespace(with_streaming_response=SimpleNamespace(create=SpeechEndpoint().create)))
            )

        client = SimpleNamespace(
            with_options=with_options
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "TTS API or audio stream failed"):
                app.make_speech(client, "Hello", Path(tmp) / "reply.mp3")
        self.assertEqual(request_options, {"timeout": 25, "max_retries": 0})
        self.assertEqual(request_args["model"], "gpt-4o-mini-tts")
        self.assertEqual(request_args["voice"], "cedar")
        self.assertEqual(request_args["instructions"], app.TTS_INSTRUCTIONS)

    def test_prompt_audio_cache_invalidates_once_and_reuses_matching_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "prompt.mp3"

            def fake_speech(client, text, output, instructions=app.TTS_INSTRUCTIONS):
                output.write_bytes(b"mock MP3 data" * 256)

            with patch.object(app, "make_speech", side_effect=fake_speech) as generate:
                with patch.object(app, "validate_speech_file"):
                    with patch.object(app, "play_mp3") as play:
                        app.play_prompt(None, "Hello there.", prompt, "listening")
                        self.assertTrue(prompt.exists())
                        self.assertTrue(app.prompt_cache_is_current(prompt, "Hello there."))
                        app.play_prompt(None, "Hello there.", prompt, "listening")
                        self.assertFalse(app.prompt_cache_is_current(prompt, "A changed prompt."))
                        app.play_prompt(None, "A changed prompt.", prompt, "listening")
                        self.assertTrue(app.prompt_cache_is_current(prompt, "A changed prompt."))
            self.assertEqual(generate.call_count, 2)
            self.assertEqual([call.args[1] for call in generate.call_args_list], [
                "Hello there.", "A changed prompt.",
            ])
            self.assertEqual(play.call_count, 3)
            self.assertEqual(
                [call.kwargs.get("normalize_loudness") for call in play.call_args_list],
                [True, True, True],
            )

    def test_both_prompt_requests_use_exact_text_and_cedar_configuration(self):
        calls = []

        class SpeechResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream_to_file(self, output):
                Path(output).write_bytes(b"mock Cedar MP3" * 256)

        class SpeechEndpoint:
            def create(self, **kwargs):
                calls.append(kwargs)
                return SpeechResponse()

        client = SimpleNamespace(
            with_options=lambda **kwargs: SimpleNamespace(
                audio=SimpleNamespace(speech=SimpleNamespace(
                    with_streaming_response=SimpleNamespace(create=SpeechEndpoint().create)
                ))
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            listening = Path(tmp) / "prompt_listening.mp3"
            thinking = Path(tmp) / "prompt_thinking.mp3"
            with patch.object(app, "validate_speech_file"):
                with patch.object(app, "play_mp3"):
                    app.play_prompt(client, app.LISTENING_PROMPT_TEXT, listening, "listening", app.LISTENING_TTS_INSTRUCTIONS)
                    app.play_prompt(client, app.THINKING_PROMPT_TEXT, thinking, "thinking")
                    app.play_prompt(client, app.LISTENING_PROMPT_TEXT, listening, "listening", app.LISTENING_TTS_INSTRUCTIONS)
        self.assertEqual(len(calls), 2)
        self.assertEqual([call["input"] for call in calls], [
            "Hey Rohan! What's up? I'm listening.", "Alright, let me think.",
        ])
        for call in calls:
            self.assertEqual(call["model"], "gpt-4o-mini-tts")
            self.assertEqual(call["voice"], "cedar")
            self.assertEqual(call["response_format"], "mp3")
        self.assertEqual([call["instructions"] for call in calls], [
            app.LISTENING_TTS_INSTRUCTIONS, app.TTS_INSTRUCTIONS,
        ])

    def test_prompt_and_final_audio_use_the_same_loudness_filter(self):
        commands = []

        def fake_run(command, description):
            commands.append(command)
            if command[0] == "ffmpeg":
                Path(command[-1]).write_bytes(b"normalized audio")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp3"
            processed = Path(tmp) / "processed.mp3"
            source.write_bytes(b"fresh Cedar audio")
            with patch.object(app, "REPLY_PLAYBACK_MP3", processed):
                with patch.object(app, "run", side_effect=fake_run):
                    with patch.object(app, "validate_speech_file"):
                        with patch.object(app, "run_cancellable", return_value=True):
                            app.play_mp3(source, "listening prompt", normalize_loudness=True)
                            app.play_mp3(source, "assistant response", normalize_loudness=True)
        filters = [command[command.index("-af") + 1] for command in commands if "-af" in command]
        self.assertEqual(filters, [
            f"loudnorm=I={app.AUDIO_TARGET_LUFS}:TP={app.AUDIO_TRUE_PEAK_DBTP:g}:LRA=11",
            f"loudnorm=I={app.AUDIO_TARGET_LUFS}:TP={app.AUDIO_TRUE_PEAK_DBTP:g}:LRA=11",
        ])

    def test_final_answer_uses_canonical_cedar_tts_and_normalized_reply_path(self):
        class UART:
            def __init__(self):
                self.states = []

            def send(self, state):
                self.states.append(state)

        request_options = {}
        request_args = {}

        class SpeechResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream_to_file(self, output):
                Path(output).write_bytes(b"fresh Cedar final reply")

        class SpeechEndpoint:
            def create(self, **kwargs):
                request_args.update(kwargs)
                return SpeechResponse()

        def with_options(**kwargs):
            request_options.update(kwargs)
            return SimpleNamespace(
                audio=SimpleNamespace(speech=SimpleNamespace(with_streaming_response=SpeechEndpoint()))
            )

        uart = UART()
        with tempfile.TemporaryDirectory() as tmp:
            reply_audio = Path(tmp) / "reply.mp3"
            reply_audio.write_bytes(b"stale Alloy reply")
            with patch.object(app, "REPLY_MP3", reply_audio):
                with patch.object(app, "validate_speech_file") as validate:
                    with patch.object(app, "play_mp3") as play:
                        app.speak_reply(SimpleNamespace(with_options=with_options), uart, "Here is the answer.")
            self.assertEqual(reply_audio.read_bytes(), b"fresh Cedar final reply")
        self.assertEqual(request_options, {"timeout": 25, "max_retries": 0})
        self.assertEqual(request_args["input"], "Here is the answer.")
        self.assertEqual(request_args["model"], "gpt-4o-mini-tts")
        self.assertEqual(request_args["voice"], "cedar")
        self.assertEqual(request_args["instructions"], app.TTS_INSTRUCTIONS)
        validate.assert_called_once_with(reply_audio)
        args, kwargs = play.call_args
        self.assertEqual(args, (reply_audio, "assistant response"))
        self.assertTrue(kwargs["normalize_loudness"])
        self.assertTrue(callable(kwargs["on_started"]))
        self.assertEqual(uart.states, [])
        kwargs["on_started"]()
        self.assertEqual(uart.states, ["SPEAKING"])

    def test_final_playback_rebuilds_normalized_file_instead_of_reusing_stale_audio(self):
        commands = []

        def fake_run(command, description):
            commands.append(command)
            if description == "Normalizing assistant response loudness":
                Path(command[-1]).write_bytes(b"new normalized Cedar audio")

        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "reply.mp3"
            processed = Path(tmp) / "reply_playback.mp3"
            raw.write_bytes(b"fresh Cedar audio")
            processed.write_bytes(b"stale alternate-voice audio")
            with patch.object(app, "REPLY_PLAYBACK_MP3", processed):
                with patch.object(app, "run", side_effect=fake_run):
                    with patch.object(app, "validate_speech_file"):
                        with patch.object(app, "run_cancellable", return_value=True):
                            app.play_mp3(raw, "assistant response", normalize_loudness=True)
            self.assertEqual(processed.read_bytes(), b"new normalized Cedar audio")
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][-1], str(processed))

    def test_conversation_context_is_bounded_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversation.json"
            messages = []
            for i in range(6):
                messages += [{"role": "user", "content": f"Question {i}"}, {"role": "assistant", "content": f"Answer {i}"}]
            app.save_conversation(messages, path)
            restored = app.load_conversation(path)
            self.assertEqual(len(restored), app.CONVERSATION_TURNS * 2)
            self.assertEqual(restored[0]["content"], "Question 2")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_timing_helper_reports_elapsed_time(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(app.timed("test stage", lambda: 42), 42)
        self.assertIn("TIMING test stage:", output.getvalue())

    def test_missing_reply_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "missing or undersized"):
                app.validate_speech_file(Path(tmp) / "missing.mp3")

    def test_missing_audio_tool_is_reported_before_startup(self):
        def which(tool):
            return None if tool == "ffmpeg" else f"/usr/bin/{tool}"

        with patch.object(app.shutil, "which", side_effect=which):
            with self.assertRaisesRegex(RuntimeError, r"Missing required tool\(s\): ffmpeg"):
                app.ensure_tools()


if __name__ == "__main__":
    unittest.main()
