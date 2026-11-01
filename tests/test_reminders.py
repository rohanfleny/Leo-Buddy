import tempfile
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from unittest.mock import patch

from reminder_store import ReminderStore
from reminder_commands import handle_reminder_command, parse_schedule, recognize_reminder_action
from reminder_scheduler import ReminderScheduler
import voice_demo


TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 13, 9, 0, tzinfo=TZ)


def _tomorrow_bounds():
    start = datetime(2026, 10, 14, 0, 0, tzinfo=TZ)
    return start, start + timedelta(days=1)


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "reminders.sqlite3"
        self.store = ReminderStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_and_persistence(self):
        due = NOW + timedelta(days=1)
        created = self.store.create("Exam", due)
        reopened = ReminderStore(self.path)
        self.assertEqual(reopened.get(created.id).title, "Exam")
        self.assertEqual(reopened.get(created.id).scheduled_at, due.astimezone(ZoneInfo("UTC")))

    def test_natural_create_date_time_and_clarifications(self):
        due, title, clarification = parse_schedule(
            "Remind me that I have a project meeting tomorrow at 10 AM", NOW
        )
        self.assertIsNone(clarification)
        self.assertEqual(title, "project meeting")
        self.assertEqual(due, datetime(2026, 10, 14, 10, 0, tzinfo=TZ))
        self.assertIn("What time", parse_schedule("Remind me about the exam tomorrow", NOW)[2])
        self.assertIn("What day", parse_schedule("Remind me about the exam at 10 AM", NOW)[2])
        self.assertEqual(
            parse_schedule("Remind me that I have project meeting tomorrow at 10", NOW)[2],
            "Do you mean 10 AM or 10 PM?",
        )
        self.assertIn("already passed", parse_schedule("Remind me about a call today at 8 AM", NOW)[2])

    def test_today_tomorrow_agenda_and_next_tasks(self):
        self.store.create("Today task", datetime(2026, 10, 13, 11, tzinfo=TZ))
        self.store.create("Tomorrow task", datetime(2026, 10, 14, 11, tzinfo=TZ))
        today = handle_reminder_command("What is my work today?", self.store, NOW)
        tomorrow = handle_reminder_command("What is my schedule for tomorrow?", self.store, NOW)
        next_three = handle_reminder_command("What are my next three tasks?", self.store, NOW)
        self.assertIn("Today task", today)
        self.assertNotIn("Tomorrow task", today)
        self.assertIn("Tomorrow task", tomorrow)
        self.assertIn("Today task", next_three)

    def test_duplicate_is_added_and_announced_as_duplicate(self):
        text = "Remind me that I have a project meeting tomorrow at 10 AM"
        first = handle_reminder_command(text, self.store, NOW)
        second = handle_reminder_command(text, self.store, NOW)
        self.assertIn("Reminder saved", first)
        self.assertIn("another reminder", second)
        self.assertEqual(len(self.store.list_pending()), 2)

    def test_ambiguous_and_id_status_reschedule(self):
        first = self.store.create("Project meeting alpha", NOW + timedelta(days=1))
        second = self.store.create("Project meeting beta", NOW + timedelta(days=2))
        self.assertIn("multiple", handle_reminder_command("Cancel project reminder", self.store, NOW))
        done = handle_reminder_command(f"Mark reminder {first.id} as completed", self.store, NOW)
        self.assertIn("completed", done)
        cancelled = handle_reminder_command(f"Cancel reminder {second.id}", self.store, NOW)
        self.assertIn("cancelled", cancelled.lower())
        self.assertEqual(self.store.get(second.id).status, "cancelled")
        target = self.store.create("One-off meeting", NOW + timedelta(days=3))
        moved = handle_reminder_command("Reschedule my One-off meeting to tomorrow at 2 PM", self.store, NOW)
        self.assertIn("2:00 PM", moved)
        self.assertEqual(self.store.get(target.id).scheduled_at, datetime(2026, 10, 14, 14, tzinfo=TZ).astimezone(ZoneInfo("UTC")))
        another = self.store.create("Review meeting", NOW + timedelta(days=3))
        moved_time_only = handle_reminder_command("Reschedule my Review meeting to 2 PM", self.store, NOW)
        self.assertIn("2:00 PM", moved_time_only)
        self.assertEqual(self.store.get(another.id).scheduled_at, datetime(2026, 10, 16, 14, tzinfo=TZ).astimezone(ZoneInfo("UTC")))

    def test_claim_due_notifications_prevents_duplicates(self):
        due = self.store.create("Due item", NOW - timedelta(minutes=1))
        scheduler = ReminderScheduler(self.store, interval_seconds=1)
        scheduler.start()
        claimed = scheduler.stop_and_claim_due()
        self.assertEqual([item.id for item in claimed], [due.id])
        self.assertEqual(self.store.claim_due_notifications([due.id], NOW), [])

    def test_due_notifications_can_be_claimed_without_stopping_scheduler(self):
        due = self.store.create("Due during conversation", datetime.now().astimezone() - timedelta(minutes=1))
        scheduler = ReminderScheduler(self.store, interval_seconds=1)
        claimed = scheduler.claim_due()
        self.assertEqual([item.id for item in claimed], [due.id])
        self.assertEqual(self.store.claim_due_notifications([due.id]), [])
        self.assertFalse(scheduler._stop.is_set())

    def test_ordinary_text_is_not_intercepted(self):
        self.assertIsNone(handle_reminder_command("Tell me a joke", self.store, NOW))

    def test_voice_integration_handles_reminder_locally_before_openai(self):
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: self.fail("unexpected API call")))
        with patch("voice_demo.handle_reminder_command", side_effect=lambda text, store: handle_reminder_command(text, store, NOW)):
            reply = voice_demo.generate_reply(
                client, "Remind me that I have a project meeting tomorrow at 10 AM", [], self.store
            )
        self.assertIn("Reminder saved", reply)
        self.assertEqual(len(self.store.list_pending()), 1)

    def test_voice_create_then_restart_and_read_tomorrow_schedule(self):
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: self.fail("unexpected API call")))
        with patch("voice_demo.handle_reminder_command", side_effect=lambda text, store: handle_reminder_command(text, store, NOW)):
            saved = voice_demo.generate_reply(
                client, "Remind me that I have project meeting tomorrow at 10 AM", [], self.store
            )
            self.assertIn("project meeting", saved)
            reopened_store = ReminderStore(self.path)
            retrieved = voice_demo.generate_reply(
                client, "What is my schedule for tomorrow?", [], reopened_store
            )
        self.assertIn("project meeting", retrieved)
        self.assertIn("10:00 AM", retrieved)

    def test_empty_tomorrow_schedule_is_truthful_and_local(self):
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: self.fail("schedule lookup reached AI")))
        with patch("voice_demo.handle_reminder_command", side_effect=lambda text, store: handle_reminder_command(text, store, NOW)):
            reply = voice_demo.generate_reply(client, "What is my schedule for tomorrow?", [], self.store)
        self.assertEqual(reply, "You have no pending reminders for tomorrow.")

    def test_failed_database_write_never_claims_saved(self):
        class FailedWriteStore:
            def matching_duplicate(self, *args):
                return None

            def create(self, *args, **kwargs):
                raise sqlite3.OperationalError("mock write failure")

        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: self.fail("DB failure reached AI")))
        with patch("voice_demo.handle_reminder_command", side_effect=lambda text, store: handle_reminder_command(text, store, NOW)):
            reply = voice_demo.generate_reply(
                client, "Remind me about an exam tomorrow at 10 AM", [], FailedWriteStore()
            )
        self.assertNotIn("saved", reply.lower())
        self.assertIn("couldn't access", reply.lower())

    def test_creation_confirmation_requires_readback_of_committed_record(self):
        class NonPersistentStore:
            def matching_duplicate(self, *args):
                return None

            def create(self, title, scheduled_at):
                return SimpleNamespace(id=91, title=title, scheduled_at=scheduled_at)

            def get(self, reminder_id):
                return None

        answer = handle_reminder_command(
            "Remind me about an exam tomorrow at 10 AM", NonPersistentStore(), NOW
        )
        self.assertNotIn("Reminder saved", answer)
        self.assertIn("couldn't verify", answer)

    def test_schedule_phrase_creates_named_meeting(self):
        due, title, clarification = parse_schedule("Schedule a meeting reminder for Friday at 2 PM", NOW)
        self.assertIsNone(clarification)
        self.assertEqual(title, "meeting")
        self.assertEqual(due, datetime(2026, 10, 16, 14, 0, tzinfo=TZ))

    def test_complete_natural_variants_and_already_completed_status(self):
        reminder = self.store.create("Project meeting", NOW + timedelta(days=1))
        reply = handle_reminder_command("Mark my project meeting as done", self.store, NOW)
        self.assertIn("completed", reply.lower())
        self.assertEqual(self.store.get(reminder.id).status, "completed")
        self.assertEqual(self.store.agenda(*_tomorrow_bounds()).__len__(), 0)
        repeated = handle_reminder_command("I've finished my project meeting; mark it completed", self.store, NOW)
        self.assertIn("already completed", repeated.lower())
        history = handle_reminder_command("Show my completed reminders", self.store, NOW)
        self.assertIn("Project meeting", history)
        reopened = ReminderStore(self.path)
        self.assertEqual(reopened.list_status("completed")[0].id, reminder.id)
        self.assertEqual(reopened.agenda(*_tomorrow_bounds()), [])

    def test_create_retrieve_and_complete_tomorrows_football_match(self):
        # The extra suffix mirrors the title currently stored in the live DB;
        # matching should still use the user's meaningful title phrase.
        reminder = self.store.create(
            "football match Mark remind me", datetime(2026, 10, 14, 11, tzinfo=TZ)
        )
        agenda = handle_reminder_command("What is my schedule for tomorrow?", self.store, NOW)
        self.assertIn("football match Mark remind me", agenda)
        action = recognize_reminder_action("Mark as done tomorrow's football match", NOW)
        self.assertEqual(action.intent, "MARK_COMPLETED")
        self.assertEqual(action.title, "football match")
        self.assertEqual(action.date.isoformat(), "2026-10-14")
        client = SimpleNamespace(responses=SimpleNamespace(
            create=lambda **kwargs: self.fail("completion command reached the AI")
        ))
        with patch("voice_demo.handle_reminder_command",
                   side_effect=lambda text, store: handle_reminder_command(text, store, NOW)):
            reply = voice_demo.generate_reply(
                client, "Mark as done tomorrow's football match", [], self.store
            )
        self.assertIn("completed", reply.lower())
        self.assertEqual(self.store.get(reminder.id).status, "completed")
        self.assertEqual(self.store.agenda(*_tomorrow_bounds()), [])
        reopened = ReminderStore(self.path)
        self.assertEqual(reopened.get(reminder.id).status, "completed")

    def test_completion_word_order_possessive_id_date_and_time_variants(self):
        cases = (
            ("Mark tomorrow's football match as done", "football match", "tomorrow", None),
            ("I've completed my football match tomorrow", "football match", "tomorrow", None),
            ("Mark the football match at 11 AM as completed", "football match", None, "11:00 AM"),
            ("Mark reminder 12 as done", "12", None, None),
        )
        for text, expected_title, expected_date, expected_clock in cases:
            with self.subTest(text=text):
                action = recognize_reminder_action(text, NOW)
                self.assertIsNotNone(action)
                self.assertEqual(action.intent, "MARK_COMPLETED")
                self.assertEqual(action.title, expected_title)
                if expected_date:
                    self.assertEqual(action.date.isoformat(), "2026-10-14")
                if expected_clock:
                    self.assertEqual(action.clock.strftime("%I:%M %p"), expected_clock)

        self.assertIn("AM or 11 PM", recognize_reminder_action(
            "Mark the football match at 11 as completed", NOW
        ).clarification)

    def test_date_qualified_completion_selects_tomorrow_and_ambiguity_is_safe(self):
        tomorrow = self.store.create("Football match", datetime(2026, 10, 14, 11, tzinfo=TZ))
        later = self.store.create("Football match", datetime(2026, 10, 15, 11, tzinfo=TZ))
        reply = handle_reminder_command("Mark as done tomorrow's football match", self.store, NOW)
        self.assertIn("completed", reply.lower())
        self.assertEqual(self.store.get(tomorrow.id).status, "completed")
        self.assertEqual(self.store.get(later.id).status, "pending")

        first = self.store.create("Football match practice", datetime(2026, 10, 14, 12, tzinfo=TZ))
        second = self.store.create("Football match final", datetime(2026, 10, 14, 13, tzinfo=TZ))
        ambiguous = handle_reminder_command("Mark tomorrow's football match as done", self.store, NOW)
        self.assertIn("multiple", ambiguous.lower())
        self.assertEqual(self.store.get(first.id).status, "pending")
        self.assertEqual(self.store.get(second.id).status, "pending")

    def test_finish_phrase_resolves_title_and_ambiguous_titles_prompt(self):
        first = self.store.create("Assignment", NOW + timedelta(days=1))
        reply = handle_reminder_command("I've finished my assignment; mark it completed", self.store, NOW)
        self.assertIn("Assignment", reply)
        self.assertEqual(self.store.get(first.id).status, "completed")
        self.store.create("Design review", NOW + timedelta(days=1))
        self.store.create("Design review follow-up", NOW + timedelta(days=2))
        ambiguous = handle_reminder_command("Mark design as done", self.store, NOW)
        self.assertIn("multiple", ambiguous.lower())

    def test_bulk_cancel_date_is_transactional_repeats_safely_and_preserves_other_days(self):
        first = self.store.create("Meeting Alpha", datetime(2026, 10, 14, 10, tzinfo=TZ))
        second = self.store.create("Meeting Beta", datetime(2026, 10, 14, 14, tzinfo=TZ))
        other_day = self.store.create("Later meeting", datetime(2026, 10, 15, 10, tzinfo=TZ))
        reply = handle_reminder_command("Clear all pending reminders for tomorrow", self.store, NOW)
        self.assertIn("Cancelled 2 reminders", reply)
        self.assertEqual(self.store.get(first.id).status, "cancelled")
        self.assertEqual(self.store.get(second.id).status, "cancelled")
        self.assertEqual(self.store.get(other_day.id).status, "pending")
        self.assertEqual(self.store.agenda(*_tomorrow_bounds()), [])
        repeated = handle_reminder_command("Cancel all reminders for tomorrow", self.store, NOW)
        self.assertIn("already cancelled", repeated.lower())
        start, end = _tomorrow_bounds()
        self.assertEqual(len(self.store.list_status("cancelled", start=start, end=end)), 2)
        reopened = ReminderStore(self.path)
        self.assertEqual(len(reopened.list_status("cancelled", start=start, end=end)), 2)
        self.assertEqual(reopened.agenda(start, end), [])
        history = handle_reminder_command("List cancelled reminders", self.store, NOW)
        self.assertIn("Meeting Alpha", history)
        self.assertIn("Meeting Beta", history)

    def test_single_cancel_by_date_and_repeated_cancel(self):
        first = self.store.create("Exam", datetime(2026, 10, 19, 8, tzinfo=TZ))
        reply = handle_reminder_command("Cancel my exam reminder next Monday", self.store, NOW)
        self.assertIn("Cancelled Exam", reply)
        repeated = handle_reminder_command("Cancel my exam reminder next Monday", self.store, NOW)
        self.assertIn("already cancelled", repeated.lower())
        self.assertEqual(self.store.get(first.id).status, "cancelled")

    def test_cancel_tomorrows_meeting_only_affects_that_date(self):
        tomorrow = self.store.create("Meeting", datetime(2026, 10, 14, 10, tzinfo=TZ))
        later = self.store.create("Meeting", datetime(2026, 10, 15, 10, tzinfo=TZ))
        reply = handle_reminder_command("Cancel tomorrow's meeting", self.store, NOW)
        self.assertIn("Cancelled Meeting", reply)
        self.assertEqual(self.store.get(tomorrow.id).status, "cancelled")
        self.assertEqual(self.store.get(later.id).status, "pending")

    def test_structured_intents_and_required_clarifications(self):
        self.assertEqual(recognize_reminder_action("Cancel tomorrow's meeting", NOW).intent, "CANCEL_REMINDER")
        self.assertEqual(recognize_reminder_action("Cancel all pending reminders for tomorrow", NOW).intent, "CANCEL_REMINDERS_FOR_DATE")
        self.assertEqual(recognize_reminder_action("List completed reminders", NOW).intent, "LIST_COMPLETED")
        self.assertEqual(recognize_reminder_action("Show cancelled reminders", NOW).intent, "LIST_CANCELLED")
        self.assertIn("Which date", recognize_reminder_action("Clear all reminders", NOW).clarification)
        self.assertIn("AM or 10 PM", recognize_reminder_action("Remind me about a meeting tomorrow at 10", NOW).clarification)
        self.assertEqual(recognize_reminder_action("What did I cancel?", NOW).intent, "LIST_CANCELLED")

    def test_database_failure_during_status_change_never_confirms_success(self):
        reminder = self.store.create("Exam", NOW + timedelta(days=1))

        class FailedTransitionStore:
            def find_matches(self, *args, **kwargs):
                return [reminder]

            def transition_status(self, *args, **kwargs):
                raise sqlite3.OperationalError("mock update failure")

        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: self.fail("DB failure reached AI")))
        with patch("voice_demo.handle_reminder_command", side_effect=lambda text, store: handle_reminder_command(text, store, NOW)):
            reply = voice_demo.generate_reply(client, "Mark my exam as completed", [], FailedTransitionStore())
        self.assertNotIn("marked", reply.lower())
        self.assertIn("couldn't access", reply.lower())


if __name__ == "__main__":
    unittest.main()
