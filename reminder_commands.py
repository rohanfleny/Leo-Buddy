"""Deterministic English reminder command parsing and formatting."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re

from reminder_store import local_timezone

WEEKDAYS = {name.lower(): index for index, name in enumerate(
    ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
)}


def _parse_clock(text: str):
    match = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)\b|\b([01]?\d|2[0-3]):([0-5]\d)\b", text, re.I)
    if not match:
        return None, None
    token = match.group(0)
    if match.group(4):
        hour, minute = int(match.group(4)), int(match.group(5))
    else:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        suffix = re.sub(r"\.", "", match.group(3)).lower()
        hour = (hour % 12) + (12 if suffix == "pm" else 0)
    return time(hour, minute), token


def _parse_date(text: str, now: datetime):
    lower = text.lower()
    if re.search(r"\btoday\b", lower):
        return now.date(), "today"
    if re.search(r"\btomorrow\b", lower):
        return now.date() + timedelta(days=1), "tomorrow"
    match = re.search(r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lower)
    if match:
        target = WEEKDAYS[match.group(1)]
        days = (target - now.weekday()) % 7 or 7
        return now.date() + timedelta(days=days), match.group(0)
    match = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lower)
    if match:
        days = (WEEKDAYS[match.group(1)] - now.weekday()) % 7
        return now.date() + timedelta(days=days), match.group(1)
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", lower)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date(), match.group(1)
        except ValueError:
            return None, match.group(1)
    return None, None


def parse_schedule(text: str, now: datetime | None = None):
    """Return (scheduled aware local datetime, title, clarification) for create requests."""
    now = (now or datetime.now().astimezone()).astimezone(local_timezone())
    date_value, date_token = _parse_date(text, now)
    clock, clock_token = _parse_clock(text)
    if not date_value:
        return None, None, "What day should I remind you?"
    if not clock:
        ambiguous_hour = re.search(
            r"\bat\s+(1[0-2]|0?[1-9])\b(?!\s*(?:a\.?m\.?|p\.?m\.?))", text, re.I
        )
        if ambiguous_hour:
            hour = int(ambiguous_hour.group(1))
            return None, None, f"Do you mean {hour} AM or {hour} PM?"
        return None, None, "What time should I set it for?"
    scheduled_phrase = re.match(r"^\s*(?:please\s+)?schedule\s+(?:a\s+)?(.+?)\s+reminder(?:\s+for)?\s*", text, re.I)
    if scheduled_phrase:
        prefix = scheduled_phrase.group(1) + " " + text[scheduled_phrase.end():]
    else:
        prefix = re.sub(
            r"^\s*(?:please\s+)?(?:remind me(?: that)?|add (?:a )?reminder(?: for)?|"
            r"set (?:a )?reminder(?: for)?|schedule\s+reminder(?:\s+for)?)\s*",
            "", text, flags=re.I,
        )
    title = prefix
    for token in (date_token, clock_token):
        if token:
            title = re.sub(re.escape(token), " ", title, flags=re.I)
    title = re.sub(r"\b(?:at|on|for|my|that i have|i have|that|to|a|an)\b", " ", title, flags=re.I)
    title = re.sub(r"[,.!?]+", " ", title)
    title = " ".join(title.split()).strip()
    if not title:
        return None, None, "What should I remind you about?"
    scheduled = datetime.combine(date_value, clock, tzinfo=now.tzinfo)
    if scheduled <= now:
        return None, None, "That time has already passed. What future day and time should I use?"
    return scheduled, title, None


def _spoken_time(reminder, tz):
    local = reminder.scheduled_at.astimezone(tz)
    return f"{local:%A, %B} {local.day} at {local:%I:%M %p}".replace(" 0", " ")


@dataclass(frozen=True)
class ReminderAction:
    intent: str
    title: str | None = None
    reminder_id: int | None = None
    scheduled_at: datetime | None = None
    clock: time | None = None
    date: date | None = None
    clarification: str | None = None
    bulk: bool = False
    history_status: str | None = None
    period: str | None = None


def _clean_target(value: str, date_token: str | None = None,
                  clock_token: str | None = None) -> tuple[str | None, int | None]:
    target = value.strip()
    if date_token:
        target = re.sub(re.escape(date_token) + r"(?:'s)?", " ", target, flags=re.I)
    if clock_token:
        target = re.sub(re.escape(clock_token), " ", target, flags=re.I)
        target = re.sub(r"\bat\b", " ", target, flags=re.I)
    target = re.sub(r"\b(?:reminder\s+)?(?:id|number)\s*#?\s*(\d+)\b", r"\1", target, flags=re.I)
    target = re.sub(r"\b(?:my|the|a|an|reminder|reminders|task|tasks|appointment|appointments)\b", " ", target, flags=re.I)
    target = re.sub(r"['’]s\b", " ", target)
    target = re.sub(r"[.,!?;:#]", " ", target)
    target = " ".join(target.split()).strip()
    if target.isdecimal():
        return target, int(target)
    return (target or None), None


def recognize_reminder_action(text: str, now: datetime | None = None) -> ReminderAction | None:
    """Parse natural command variants into a validated local action; never emits SQL."""
    raw = text.strip()
    lower = raw.lower()
    if not raw:
        return None
    local_now = (now or datetime.now().astimezone()).astimezone(local_timezone())
    date_value, date_token = _parse_date(raw, local_now)

    # History queries must precede completion verbs (e.g. "what have I completed?").
    if re.search(r"\b(?:show|list|tell me|what|which)\b.*\b(?:completed|finished|done)\b|\bwhat have i (?:completed|finished|done)\b", lower):
        return ReminderAction("LIST_COMPLETED", date=date_value, history_status="completed")
    if re.search(r"\b(?:show|list|tell me|what|which)\b.*\b(?:cancelled|canceled)\b|\bwhat did i cancel\b", lower):
        return ReminderAction("LIST_CANCELLED", date=date_value, history_status="cancelled")

    reschedule = re.search(r"\breschedule\b", lower)
    if reschedule:
        remainder = raw[reschedule.end():].strip()
        split = re.search(r"\bto\b", remainder, re.I)
        if not split:
            return ReminderAction("RESCHEDULE", clarification="What new day or time should I use?")
        target, reminder_id = _clean_target(remainder[:split.start()])
        if not target:
            return ReminderAction("RESCHEDULE", clarification="Which reminder should I reschedule?")
        date_phrase = remainder[split.end():]
        new_date, _ = _parse_date(date_phrase, local_now)
        clock, _ = _parse_clock(date_phrase)
        if not clock:
            ambiguous_hour = re.search(r"\bat\s+(1[0-2]|0?[1-9])\b(?!\s*(?:a\.?m\.?|p\.?m\.?))", date_phrase, re.I)
            if ambiguous_hour:
                hour = int(ambiguous_hour.group(1))
                return ReminderAction("RESCHEDULE", title=target, reminder_id=reminder_id, clarification=f"Do you mean {hour} AM or {hour} PM?")
        if not new_date and not clock:
            return ReminderAction("RESCHEDULE", title=target, reminder_id=reminder_id, clarification="What new day or time should I use?")
        return ReminderAction("RESCHEDULE", title=target, reminder_id=reminder_id, date=new_date, clock=clock)

    cancel_match = re.search(r"\b(cancel|cancelled|canceled|delete|remove|clear)\b", lower)
    if cancel_match:
        bulk = bool(re.search(r"\b(?:all|every|everything)\b", lower))
        if bulk:
            if not date_value:
                return ReminderAction("CANCEL_REMINDERS_FOR_DATE", clarification="Which date should I clear reminders for?")
            return ReminderAction("CANCEL_REMINDERS_FOR_DATE", date=date_value, bulk=True)
        remainder = raw[cancel_match.end():]
        remainder = re.sub(r"^\s+(?:all\s+)?(?:my\s+)?", "", remainder, flags=re.I)
        remainder = re.sub(r"\s+\b(?:scheduled|set)\b.*$", "", remainder, flags=re.I)
        title, reminder_id = _clean_target(remainder, date_token)
        if not title:
            if date_value:
                return ReminderAction("CANCEL_REMINDER", date=date_value, clarification="Which reminder on that date should I cancel? Or say ‘cancel all reminders’ for that date.")
            return ReminderAction("CANCEL_REMINDER", clarification="Which reminder should I cancel?")
        return ReminderAction("CANCEL_REMINDER", title=title, reminder_id=reminder_id, date=date_value)

    if re.search(r"\b(?:mark|complete|finish)\s+all\b", lower):
        return ReminderAction("MARK_COMPLETED", clarification="Which specific reminder should I mark completed?")
    finished = re.search(r"\b(?:i['’]?ve|i have)\s+(?:finished|completed|done)\s+(?:my\s+)?(.+?)(?=[.;,]|$)", raw, flags=re.I)
    marked = re.search(r"\bmark\s+(?:my\s+)?(?:reminder\s+)?(.+?)\s+(?:as\s+)?(?:done|completed|complete|finished)\b", raw, flags=re.I)
    # Also accept the common spoken inversion “mark as done <target>”.
    # The earlier target-first pattern does not match this word order.
    status_first = re.search(
        r"\bmark\s+(?:as\s+)?(?:done|completed|complete|finished)\s+(.+?)\s*[.!?]*$",
        raw, flags=re.I,
    )
    direct = re.search(r"\b(?:complete|finish)\s+(?:my\s+)?(?:reminder\s+)?(.+)$", raw, flags=re.I)
    if finished or marked or status_first or direct:
        target_text = (finished.group(1) if finished else
                       status_first.group(1) if status_first else
                       marked.group(1) if marked else direct.group(1))
        clock, clock_token = _parse_clock(raw)
        ambiguous_hour = re.search(
            r"\bat\s+(1[0-2]|0?[1-9])\b(?!\s*(?:a\.?m\.?|p\.?m\.?))", raw, re.I
        )
        if not clock and ambiguous_hour:
            hour = int(ambiguous_hour.group(1))
            return ReminderAction("MARK_COMPLETED", clarification=f"Do you mean {hour} AM or {hour} PM?")
        target, reminder_id = _clean_target(target_text, date_token, clock_token)
        if not target or target.lower() in {"it", "that", "this"}:
            return ReminderAction("MARK_COMPLETED", clarification="Which reminder should I mark completed?")
        return ReminderAction("MARK_COMPLETED", title=target, reminder_id=reminder_id,
                              date=date_value, clock=clock)

    if re.search(r"\b(remind me|add (?:a )?reminder|set (?:a )?reminder|schedule .+ reminder)\b", lower):
        scheduled, title, clarification = parse_schedule(raw, local_now)
        return ReminderAction("CREATE_REMINDER", title=title, scheduled_at=scheduled, clarification=clarification)

    agenda_query = (bool(re.search(r"\b(?:schedule|agenda)\b", lower)) or bool(re.search(r"\bmeetings?\b", lower))) and bool(
        re.search(r"\b(?:what|show|list|tell me|do i have|have i got)\b", lower)
    )
    if agenda_query or any(term in lower for term in (
        "my schedule", "schedule for", "my work", "pending tasks", "next three tasks",
        "what do i have", "what's on my schedule", "what is on my schedule",
        "meetings do i have", "upcoming reminder",
    )):
        period = (
            "next_three" if "next three tasks" in lower else
            "upcoming" if "upcoming reminder" in lower else
            "pending" if "pending task" in lower else
            "week" if re.search(r"\b(?:this week|week)\b", lower) else None
        )
        return ReminderAction("LIST_SCHEDULE", date=date_value, period=period)
    return None


def _date_bounds(day, tz):
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _format_items(items, tz):
    return "; ".join(f"{item.title}, {_spoken_time(item, tz)}" for item in items)


def handle_reminder_command(text: str, store, now: datetime | None = None):
    """Recognize, validate, and execute a reminder action; None means ordinary chat."""
    tz = local_timezone()
    local_now = (now or datetime.now().astimezone()).astimezone(tz)
    action = recognize_reminder_action(text, local_now)
    if action is None:
        return None
    if action.clarification:
        return action.clarification
    if store is None:
        return "Reminder storage is unavailable right now, so I haven't changed anything."

    try:
        if action.intent == "CREATE_REMINDER":
            duplicate = store.matching_duplicate(action.title, action.scheduled_at)
            created = store.create(action.title, action.scheduled_at)
            persisted = store.get(created.id) if created is not None else None
            if persisted is None or persisted.status != "pending" or persisted.title != action.title or persisted.scheduled_at != created.scheduled_at:
                return "I couldn't verify that reminder was saved, so I haven't confirmed it."
            prefix = "I added another reminder" if duplicate else "Reminder saved"
            return f"{prefix}: {persisted.title}, {_spoken_time(persisted, tz)}."

        if action.intent == "RESCHEDULE":
            target = action.title or str(action.reminder_id or "")
            matches = store.find_pending(target)
            if len(matches) > 1:
                return "I found multiple matching reminders. Please specify the reminder ID or full title."
            if not matches:
                return "I couldn't find a pending reminder with that title."
            old_local = matches[0].scheduled_at.astimezone(tz)
            day = action.date or old_local.date()
            clock = action.clock or old_local.timetz().replace(tzinfo=None)
            scheduled = datetime.combine(day, clock, tzinfo=tz)
            if scheduled <= local_now:
                return "That new time has already passed. What future day and time should I use?"
            updated = store.reschedule(matches[0].id, scheduled)
            if updated is None or updated.scheduled_at != scheduled.astimezone(updated.scheduled_at.tzinfo):
                return "I couldn't verify the new reminder time, so I haven't confirmed the change."
            return f"Rescheduled {updated.title} to {_spoken_time(updated, tz)}."

        if action.intent == "LIST_SCHEDULE":
            if action.period == "next_three":
                items = store.next_pending(3)
                return "Your next tasks are: " + _format_items(items, tz) + "." if items else "You have no pending reminders."
            if action.period == "upcoming":
                items = store.next_pending(1)
                return f"Your next reminder is {_format_items(items, tz)}." if items else "You have no pending reminders."
            if action.period == "pending":
                items = store.list_pending()
                return "Your pending reminders are: " + _format_items(items, tz) + "." if items else "You have no pending reminders."
            day = action.date or local_now.date()
            if action.period == "week":
                start = datetime.combine(local_now.date(), time.min, tzinfo=tz)
                end = start + timedelta(days=7 - start.weekday())
            else:
                start, end = _date_bounds(day, tz)
            items = store.agenda(start, end)
            if not items:
                period = "this week" if action.period == "week" else "tomorrow" if day == local_now.date() + timedelta(days=1) else "today" if day == local_now.date() else day.strftime("%A, %B") + f" {day.day}"
                return f"You have no pending reminders for {period}."
            heading = "Your schedule this week" if action.period == "week" else "Your schedule" if day == local_now.date() else f"Your schedule for {day:%A, %B} {day.day}"
            return heading + ": " + _format_items(items, tz) + "."

        if action.intent in {"LIST_COMPLETED", "LIST_CANCELLED"}:
            if action.history_status:
                if action.date:
                    start, end = _date_bounds(action.date, tz)
                else:
                    start = end = None
                items = store.list_status(action.history_status, start=start, end=end)
                label = "completed" if action.history_status == "completed" else "cancelled"
                return f"Your {label} reminders are: {_format_items(items, tz)}." if items else f"You have no {label} reminders in that period."

        if action.intent == "CANCEL_REMINDERS_FOR_DATE":
            start, end = _date_bounds(action.date, tz)
            changed = store.cancel_pending_between(start, end)
            if changed:
                noun = "reminder" if changed == 1 else "reminders"
                return f"Cancelled {changed} {noun} for {action.date:%A, %B} {action.date.day}."
            already = store.list_status("cancelled", start=start, end=end)
            return "Those reminders are already cancelled." if already else "There were no pending reminders on that date."

        if action.intent in {"MARK_COMPLETED", "CANCEL_REMINDER"}:
            target = action.title or str(action.reminder_id or "")
            start, end = _date_bounds(action.date, tz) if action.date else (None, None)
            pending = store.find_matches(target, statuses=("pending",), start=start, end=end)
            if action.clock is not None:
                pending = [item for item in pending
                           if item.scheduled_at.astimezone(tz).timetz().replace(tzinfo=None) == action.clock]
            if len(pending) > 1:
                return "I found multiple matching reminders. Please specify the reminder ID or full title."
            if not pending:
                matches = store.find_matches(target, start=start, end=end)
                if action.clock is not None:
                    matches = [item for item in matches
                               if item.scheduled_at.astimezone(tz).timetz().replace(tzinfo=None) == action.clock]
                if len(matches) > 1:
                    if action.intent == "CANCEL_REMINDER" and all(item.status == "cancelled" for item in matches):
                        return f"All {len(matches)} matching reminders are already cancelled."
                    if action.intent == "MARK_COMPLETED" and all(item.status == "completed" for item in matches):
                        return f"All {len(matches)} matching reminders are already completed."
                    return "I found multiple matching reminders in your history. Please specify the reminder ID or full title."
                if not matches:
                    return "I couldn't find that reminder in your reminder history."
                current = matches[0]
                if current.status == "completed":
                    return f"{current.title} is already completed." if action.intent == "MARK_COMPLETED" else f"{current.title} is already completed, so I left it unchanged."
                if current.status == "cancelled":
                    return f"{current.title} is already cancelled." if action.intent == "CANCEL_REMINDER" else f"{current.title} is cancelled; I didn't mark it completed."
                return "I couldn't find a pending reminder to update."
            current = pending[0]
            status = "completed" if action.intent == "MARK_COMPLETED" else "cancelled"
            updated, changed = store.transition_status(current.id, status)
            if changed != 1 or updated is None or updated.status != status:
                return f"I couldn't verify that {current.title} was {status}; no success was reported."
            return f"Marked {updated.title} as completed." if status == "completed" else f"Cancelled {updated.title}."
    except (OSError, ValueError):
        return "I couldn't update that reminder. Please check the date and title and try again."
    return None
