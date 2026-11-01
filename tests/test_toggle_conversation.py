import os
import io
import contextlib
import pty
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import voice_demo as app


def extract_cpp_function(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for position in range(opening, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[start:position + 1]
    raise AssertionError(f"unterminated function: {signature}")


class ToggleConversationTests(unittest.TestCase):
    def test_switch_sync_does_not_start_and_either_edge_starts_while_idle(self):
        gate = app.SwitchConversationGate()
        self.assertEqual(gate.handle("SWITCH:STATE:HIGH"), "synced")
        self.assertEqual(gate.handle("SWITCH:STATE:LOW"), "synced")
        self.assertFalse(gate.active)
        self.assertEqual(gate.handle("SWITCH:EDGE:HIGH_TO_LOW"), "start")
        gate.finish("cancelled")
        self.assertEqual(gate.handle("SWITCH:EDGE:LOW_TO_HIGH"), "start")

    def test_switch_edges_during_active_session_are_ignored(self):
        gate = app.SwitchConversationGate()
        self.assertEqual(gate.handle("SWITCH:EDGE:LOW_TO_HIGH"), "start")
        self.assertEqual(gate.handle("SWITCH:EDGE:HIGH_TO_LOW", "LISTENING"), "ignored_active")
        self.assertTrue(gate.active)
        gate.finish("silence_timeout")
        self.assertEqual(gate.handle("SWITCH:EDGE:HIGH_TO_LOW"), "start")

    def test_transition_while_non_idle_does_not_start_a_session(self):
        gate = app.SwitchConversationGate()
        self.assertEqual(gate.handle("SWITCH:EDGE:HIGH_TO_LOW", "THINKING"), "ignored_active")
        self.assertFalse(gate.active)

    def test_silence_timeout_returns_idle_until_any_next_physical_edge(self):
        gate = app.SwitchConversationGate()
        gate.handle("SWITCH:EDGE:HIGH_TO_LOW")
        gate.finish("silence_timeout")
        self.assertEqual(gate.handle("SWITCH:EDGE:LOW_TO_HIGH"), "start")

    def test_vad_requires_confirmed_onset_and_finishes_after_tail_silence(self):
        detector = app.SpeechActivityDetector(started_at=0.0, onset_timeout=10.0)
        quiet = struct.pack("<h", 0) * 320
        voice = struct.pack("<h", 5000) * 320
        self.assertIsNone(detector.observe(quiet, 0.02))
        self.assertIsNone(detector.observe(voice, 0.04))
        self.assertEqual(detector.observe(voice, 0.06), "started")
        self.assertIsNone(detector.observe(quiet, 0.40))
        self.assertIsNone(detector.observe(quiet, 1.25))
        self.assertIsNone(detector.observe(quiet, 1.99))
        self.assertLess(detector.post_onset_audio_seconds, 2.0)
        state = None
        for index in range(1, 110):
            state = detector.observe(quiet, 2.0 + index * 0.02)
            if state == "ended":
                break
        self.assertEqual(state, "ended")

    def test_vad_does_not_cut_off_question_after_brief_onset_pause(self):
        detector = app.SpeechActivityDetector(started_at=0.0, onset_timeout=10.0)
        quiet = struct.pack("<h", 0) * 320
        voice = struct.pack("<h", 5000) * 320
        detector.observe(quiet, 0.02)
        detector.observe(voice, 0.04)
        self.assertEqual(detector.observe(voice, 0.06), "started")
        for index in range(1, 48):
            self.assertIsNone(detector.observe(quiet, 0.06 + index * 0.02))
        # Speech resumes before the 2 s minimum, then its natural trailing
        # pause is allowed to complete the capture after the minimum.
        self.assertEqual(detector.observe(voice, 1.02), "speech")
        state = None
        for index in range(1, 100):
            state = detector.observe(quiet, 1.02 + index * 0.02)
            if state == "ended":
                break
        self.assertEqual(state, "ended")

    def test_vad_requires_pcm_duration_not_elapsed_wall_time(self):
        detector = app.SpeechActivityDetector(started_at=0.0, onset_timeout=10.0)
        quiet = struct.pack("<h", 0) * 320
        voice = struct.pack("<h", 5000) * 320
        detector.observe(quiet, 0.02)
        detector.observe(voice, 0.04)
        self.assertEqual(detector.observe(voice, 0.06), "started")
        # A long scheduler gap cannot turn a 20 ms capture into a complete utterance.
        self.assertIsNone(detector.observe(quiet, 3.0))
        self.assertLess(detector.post_onset_audio_seconds, app.MIN_UTTERANCE_CAPTURE_AFTER_ONSET_SECONDS)

    def test_vad_accepts_quiet_speech_inside_recording_validator_range(self):
        detector = app.SpeechActivityDetector(started_at=0.0, onset_timeout=10.0)
        quiet_speech = struct.pack("<h", 300) * 320
        self.assertIsNone(detector.observe(quiet_speech, 0.02))
        self.assertEqual(detector.observe(quiet_speech, 0.04), "started")

    def test_ten_seconds_without_voice_times_out_and_noise_does_not_reset_it(self):
        detector = app.SpeechActivityDetector(started_at=2.0, onset_timeout=10.0)
        quiet = struct.pack("<h", 0) * 320
        self.assertIsNone(detector.observe(quiet, 11.99))
        self.assertEqual(detector.observe(quiet, 12.0), "timeout")

    def test_partial_listening_retry_uses_remaining_deadline_not_fresh_ten_seconds(self):
        self.assertFalse(app.onset_deadline_expired(100.0, 103.9, 4.0))
        self.assertTrue(app.onset_deadline_expired(100.0, 104.0, 4.0))
        self.assertTrue(app.onset_deadline_expired(100.0, 100.0, 0.0))

    def test_arecord_uses_a_complete_wav_window_and_supplied_deadline(self):
        command = app.record_command(Path("capture.wav"), duration_seconds=10)
        self.assertEqual(command[:3], ["arecord", "-D", app.MIC_DEVICE])
        self.assertEqual(command[command.index("-d") + 1], "10")
        source = (Path(app.PROJECT_DIR) / "voice_demo.py").read_text(encoding="utf-8")
        capture = source[source.index("def record_utterance("):source.index("def local_time_reply(")]
        self.assertIn("record_command(INPUT_WAV, capture_seconds)", capture)
        self.assertIn("inspect_wav_onset(INPUT_WAV, onset_timeout)", capture)
        self.assertNotIn('"-t", "raw"', capture)

    def test_completed_wav_onset_detects_speech_and_ignores_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "capture.wav"
            quiet = struct.pack("<h", 0) * 320
            voice = struct.pack("<h", 5000) * 320
            with wave.open(str(audio_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(quiet * 20 + voice * 3 + quiet * 20)
            detector = app.inspect_wav_onset(audio_path, 10.0)
            self.assertTrue(detector.started)
            self.assertGreaterEqual(detector.speech_started_at, 0.0)
            with wave.open(str(audio_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(quiet * 100)
            self.assertFalse(app.inspect_wav_onset(audio_path, 10.0).started)

    def test_record_utterance_transcribes_a_complete_wav_instead_of_killing_raw_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "capture.wav"
            boosted_path = Path(tmp) / "boosted.wav"
            speech = struct.pack("<h", 6000) * (16000 * 2)

            def record_mock(command, _description, _cancel_event):
                self.assertEqual(command[command.index("-d") + 1], "10")
                with wave.open(command[-1], "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(16000)
                    output.writeframes(speech + speech + speech + speech + speech)
                return True

            def normalize_mock(source, destination, _cancel_event):
                destination.write_bytes(Path(source).read_bytes())
                return True

            with patch.object(app, "INPUT_WAV", audio_path), patch.object(app, "BOOSTED_WAV", boosted_path):
                with patch.object(app, "run_cancellable", side_effect=record_mock):
                    with patch.object(app, "normalize_recording", side_effect=normalize_mock):
                        self.assertTrue(app.record_utterance(__import__("threading").Event(), 10.0))
            with wave.open(str(audio_path), "rb") as recording:
                self.assertEqual(recording.getnframes() / recording.getframerate(), 10.0)

    def test_subsecond_vad_burst_is_not_sent_to_transcription(self):
        self.assertFalse(app.usable_utterance_duration(0.12))
        self.assertTrue(app.usable_utterance_duration(0.25))
        source = (Path(app.PROJECT_DIR) / "voice_demo.py").read_text(encoding="utf-8")
        self.assertIn("transcribing full WAV", source)
        self.assertIn("Recording saved:", source)
        self.assertIn("Recording too short (", source)

    def test_uart_reads_switch_events_without_changing_existing_state_lines(self):
        master_fd, slave_fd = pty.openpty()
        sender = app.UartStateSender(os.ttyname(slave_fd))
        try:
            self.assertTrue(sender.open())
            self.assertTrue(sender.send("LISTENING"))
            self.assertEqual(os.read(master_fd, 32), b"LISTENING\n")
            os.write(master_fd, b"diagnostic\nSWITCH:EDGE:HIGH_TO_LOW\nSWITCH:EDGE:LOW_TO_HIGH\n")
            self.assertEqual(sender.poll_events(), [
                "SWITCH:EDGE:HIGH_TO_LOW", "SWITCH:EDGE:LOW_TO_HIGH",
            ])
            self.assertTrue(sender.send("THINKING"))
            self.assertEqual(os.read(master_fd, 32), b"THINKING\n")
        finally:
            sender.close()
            os.close(master_fd)
            os.close(slave_fd)

    def test_uart_discards_only_oversized_line_and_recovers_at_next_newline(self):
        master_fd, slave_fd = pty.openpty()
        sender = app.UartStateSender(os.ttyname(slave_fd))
        output = io.StringIO()
        try:
            self.assertTrue(sender.open())
            # Firmware diagnostics can be longer than protocol events. Their
            # bytes must be discarded through LF without poisoning the next
            # valid edge, even when the edge arrives in a later USB read.
            os.write(master_fd, b"X" * (app.MAX_ESP32_SERIAL_LINE_BYTES + 40))
            self.assertEqual(sender.poll_events(), [])
            os.write(master_fd, b"\nSWITCH:EDGE:HIGH_TO_LOW\n")
            with contextlib.redirect_stdout(output):
                events = sender.poll_events()
            self.assertEqual(events, ["SWITCH:EDGE:HIGH_TO_LOW"])
            self.assertEqual(output.getvalue().count("discarded one oversized"), 1)
            os.write(master_fd, b"SWITCH:EDGE:LOW_TO_HIGH\n")
            self.assertEqual(sender.poll_events(), ["SWITCH:EDGE:LOW_TO_HIGH"])
        finally:
            sender.close()
            os.close(master_fd)
            os.close(slave_fd)

    def test_new_eyes_firmware_emits_newline_switch_events_and_local_listening(self):
        source = (Path(app.PROJECT_DIR) / "firmware/LeoBuddy/LeoBuddy.ino").read_text(encoding="utf-8")
        switch_handler = extract_cpp_function(source, "void pollSwitch(uint32_t now)")
        self.assertIn('Serial.printf("SWITCH:EDGE:%s\\n", edge)', switch_handler)
        self.assertIn("if (state == State::Idle)", switch_handler)
        self.assertIn("setState(State::Listening)", switch_handler)
        self.assertIn('Serial.printf("LEO:ANIM:frames=', source)

    def test_session_greets_once_and_loops_listen_think_speak_listen(self):
        class UART:
            def __init__(self):
                self.states = []

            def send(self, state):
                self.states.append(state)

        uart = UART()
        event = __import__("threading").Event()
        prompts = []

        def play_prompt(_client, text, _path, label, *args, **kwargs):
            prompts.append((label, text))
            return True

        def speak(_client, target_uart, _reply, **_kwargs):
            target_uart.send("SPEAKING")
            return True

        with patch.object(app, "load_conversation", return_value=[]):
            with patch.object(app, "save_conversation"):
                with patch.object(app, "play_prompt", side_effect=play_prompt):
                    with patch.object(app, "record_utterance", side_effect=[True, True, None]):
                        with patch.object(app, "transcribe", side_effect=["question one", "follow-up"]):
                            with patch.object(app, "generate_reply", side_effect=["answer one", "answer two"]):
                                with patch.object(app, "speak_reply", side_effect=speak):
                                    result = app.run_conversation_session(None, uart, None, None, event)
        self.assertEqual(result, "silence_timeout")
        self.assertEqual(prompts, [
            ("listening", "Hey Rohan! What's up? I'm listening."),
            ("thinking", app.THINKING_PROMPT_TEXT),
            ("thinking", app.THINKING_PROMPT_TEXT),
        ])
        self.assertEqual(uart.states, [
            "LISTENING", "THINKING", "SPEAKING", "LISTENING",
            "THINKING", "SPEAKING", "LISTENING",
        ])

    def test_empty_transcription_stays_listening_without_thinking_or_answer(self):
        class UART:
            def __init__(self):
                self.states = []

            def send(self, state):
                self.states.append(state)

        uart = UART()
        prompts = []
        event = __import__("threading").Event()
        recorder = Mock(side_effect=[True, None])

        def play_prompt(_client, _text, _path, label, *args, **kwargs):
            prompts.append(label)
            return True

        with patch.object(app, "load_conversation", return_value=[]):
            with patch.object(app, "play_prompt", side_effect=play_prompt):
                # Simulate a false/noisy onset followed by the remaining listen
                # window timing out. It must not reset the window or advance state.
                with patch.object(app, "record_utterance", recorder):
                    with patch.object(app, "transcribe", side_effect=RuntimeError("Speech recognition returned no text")):
                        with patch.object(app, "generate_reply") as generate:
                            with patch.object(app, "speak_reply") as speak:
                                # The deadline expires during the first capture.
                                # An empty transcript must not restart it.
                                with patch.object(app.time, "monotonic", side_effect=[0, 0, 0, 0, 10]):
                                    result = app.run_conversation_session(None, uart, None, None, event)

        self.assertEqual(result, "silence_timeout")
        self.assertEqual(uart.states, ["LISTENING"])
        self.assertEqual(prompts, ["listening"])
        generate.assert_not_called()
        speak.assert_not_called()
        self.assertEqual(recorder.call_count, 1)

    def test_switch_off_after_response_generation_suppresses_stale_speech(self):
        class UART:
            def __init__(self):
                self.states = []

            def send(self, state):
                self.states.append(state)

        uart = UART()
        event = __import__("threading").Event()

        def cancel_during_generation(*_args):
            event.set()
            return "stale answer must not be spoken"

        with patch.object(app, "load_conversation", return_value=[]):
            with patch.object(app, "play_prompt", return_value=True):
                with patch.object(app, "record_utterance", return_value=True):
                    with patch.object(app, "transcribe", return_value="a question"):
                        with patch.object(app, "generate_reply", side_effect=cancel_during_generation):
                            with patch.object(app, "speak_reply") as speak:
                                result = app.run_conversation_session(None, uart, None, None, event)
        self.assertEqual(result, "cancelled")
        speak.assert_not_called()

    def test_toggle_firmware_preserves_hardware_and_state_protocol(self):
        path = Path(app.PROJECT_DIR) / "firmware/LeoBuddy/LeoBuddy.ino"
        firmware = path.read_text(encoding="utf-8")
        for required in (
            "SDA_PIN = 21", "SCL_PIN = 22", "OLED_ADDRS[] = {0x3C, 0x3D}",
            "PAN_PIN = 18", "TILT_PIN = 19", "SWITCH_PIN = 23", "SERIAL_BAUD = 115200",
            "INPUT_PULLUP", "SWITCH:STATE:", "SWITCH:EDGE:%s", "sendBuffer()",
            "setPose(90, 90", "panPos[] = {85, 95, 95, 85}",
        ):
            self.assertIn(required, firmware)
        for state in ("Idle", "Listening", "Thinking", "Speaking", "Error"):
            self.assertIn(f"State::{state}", firmware)

    def test_idle_switch_edge_immediately_requests_listening_and_reports_pi_event(self):
        path = Path(app.PROJECT_DIR) / "firmware/LeoBuddy/LeoBuddy.ino"
        firmware = path.read_text(encoding="utf-8")
        switch_handler = extract_cpp_function(firmware, "void pollSwitch(uint32_t now)")
        self.assertIn("if (state == State::Idle)", switch_handler)
        self.assertIn("setState(State::Listening)", switch_handler)
        self.assertIn('Serial.printf("SWITCH:EDGE:%s\\n", edge)', switch_handler)
        self.assertIn("LEO:SWITCH_ACCEPTED:IDLE", firmware)
        self.assertIn("GPIO23 edge accepted while Pi state is IDLE", Path(app.PROJECT_DIR, "voice_demo.py").read_text())

    def test_selected_animation_blink_scheduler_and_state_rendering(self):
        path = Path(app.PROJECT_DIR) / "firmware/LeoBuddy/LeoBuddy.ino"
        firmware = path.read_text(encoding="utf-8")
        self.assertIn("FRAME_MS = 34", firmware)
        self.assertIn("BLINK_CLOSE_MS = 100", firmware)
        self.assertIn("BLINK_HOLD_MS = 170", firmware)
        self.assertIn("BLINK_OPEN_MS = 170", firmware)
        self.assertIn("BLINK_MIN_MS = 2200", firmware)
        self.assertIn("BLINK_MAX_MS = 4300", firmware)
        blink = extract_cpp_function(firmware, "float blinkAmount(uint32_t now)")
        self.assertIn("scheduleBlink(now)", blink)
        draw = extract_cpp_function(firmware, "void drawFace(uint32_t now)")
        self.assertIn("blinkAmount(now)", draw)
        self.assertIn("oled.sendBuffer()", draw)
        for expression in ("State::Listening", "State::Thinking", "State::Speaking", "State::Error"):
            self.assertIn(expression, draw)
        loop_body = extract_cpp_function(firmware, "void loop()")
        for service in ("pollSerial()", "pollSwitch(now)", "updateServos(now)", "drawFace(now)"):
            self.assertIn(service, loop_body)
        self.assertIn("drawFace(millis())", firmware)

    def test_mic_inventory_diagnostic_reports_configured_device_and_arecord_listing(self):
        fake_result = type("Result", (), {
            "stdout": "card 2: Device [USB Microphone], device 0: USB Audio [USB Audio]",
            "stderr": "", "returncode": 0,
        })()
        with patch.object(app.subprocess, "run", return_value=fake_result) as run:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                app.report_microphone_configuration()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["arecord", "-l"])
        self.assertIn(app.MIC_DEVICE, output.getvalue())
        self.assertIn("USB Microphone", output.getvalue())

    def test_final_working_servo_mapping_matches_reconstructed_reference(self):
        candidate_text = (Path(app.PROJECT_DIR) / "firmware/LeoBuddy/LeoBuddy.ino").read_text(encoding="utf-8")
        for required in ("panPos[] = {85, 95, 95, 85}", "tiltPos[] = {90, 85, 95, 90}",
                         "setPose(90, 95", "setPose(95, 85", "setPose(85, 85"):
            self.assertIn(required, candidate_text)

    def test_uart_reconnect_failure_is_reported_not_silently_dropped(self):
        self.assertIn("WARNING: unable to reopen ESP32 serial port", Path(app.PROJECT_DIR, "voice_demo.py").read_text())


if __name__ == "__main__":
    unittest.main()
