"""Small persistent SQLite store for user-created LEO reminders."""
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "leo" / "reminders.sqlite3"
STATUSES = {"pending", "completed", "cancelled"}


def local_timezone():
    """Return the configured system timezone when identifiable, else local tzinfo."""
    timezone_file = Path("/etc/timezone")
    try:
        name = timezone_file.read_text(encoding="utf-8").strip()
        if name:
            return ZoneInfo(name)
    except (OSError, ZoneInfoNotFoundError):
        pass
    try:
        localtime = Path("/etc/localtime").resolve()
        marker = "/zoneinfo/"
        if marker in str(localtime):
            return ZoneInfo(str(localtime).split(marker, 1)[1])
    except (OSError, ZoneInfoNotFoundError):
        pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Reminder timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class Reminder:
    id: int
    title: str
    details: str | None
    scheduled_at: datetime
    created_at: datetime
    status: str
    last_notified_at: datetime | None
    updated_at: datetime


def _from_row(row: sqlite3.Row | None) -> Reminder | None:
    if row is None:
        return None
    return Reminder(
        id=row["id"], title=row["title"], details=row["details"],
        scheduled_at=_datetime(row["scheduled_at"]), created_at=_datetime(row["created_at"]),
        status=row["status"], last_notified_at=_datetime(row["last_notified_at"]),
        updated_at=_datetime(row["updated_at"]),
    )


class ReminderStore:
    """Owns reminder persistence; all stored timestamps are UTC ISO-8601."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.db_path.parent, 0o700)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                    details TEXT,
                    scheduled_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'completed', 'cancelled')),
                    last_notified_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(status, scheduled_at, last_notified_at);
                """
            )
            connection.commit()
        finally:
            connection.close()
        os.chmod(self.db_path, 0o600)

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _connection(self):
        """Commit/rollback like a connection context and always close the handle."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, title: str, scheduled_at: datetime, details: str | None = None,
               created_at: datetime | None = None) -> Reminder:
        title = title.strip()
        if not title or len(title) > 300:
            raise ValueError("Reminder title must contain 1 to 300 characters")
        scheduled = _timestamp(scheduled_at)
        created = _timestamp(created_at or datetime.now().astimezone())
        updated = created
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO reminders(title, details, scheduled_at, created_at, status, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (title, details.strip() if details and details.strip() else None, scheduled, created, updated),
            )
            row = connection.execute("SELECT * FROM reminders WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return _from_row(row)

    def get(self, reminder_id: int) -> Reminder | None:
        with self._connection() as connection:
            return _from_row(connection.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone())

    def list_pending(self, limit: int = 100) -> list[Reminder]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE status='pending' ORDER BY scheduled_at, id LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def agenda(self, start: datetime, end: datetime) -> list[Reminder]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE status='pending' AND scheduled_at >= ? AND scheduled_at < ? "
                "ORDER BY scheduled_at, id",
                (_timestamp(start), _timestamp(end)),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def next_pending(self, limit: int = 1) -> list[Reminder]:
        return self.list_pending(limit)

    def find_pending(self, title_or_id: str) -> list[Reminder]:
        return self.find_matches(title_or_id, statuses=("pending",))

    def find_matches(self, title_or_id: str, statuses=None, start: datetime | None = None,
                     end: datetime | None = None) -> list[Reminder]:
        """Find by numeric ID, exact title, or unique-able title fragment."""
        target = title_or_id.strip()
        selected = tuple(statuses or sorted(STATUSES))
        if not selected or any(status not in STATUSES for status in selected):
            raise ValueError("Invalid reminder status filter")
        status_marks = ",".join("?" for _ in selected)
        date_clause = ""
        date_params = []
        if start is not None:
            date_clause += " AND scheduled_at >= ?"
            date_params.append(_timestamp(start))
        if end is not None:
            date_clause += " AND scheduled_at < ?"
            date_params.append(_timestamp(end))
        with self._connection() as connection:
            if target.isdecimal():
                rows = connection.execute(
                    f"SELECT * FROM reminders WHERE id = ? AND status IN ({status_marks}){date_clause} "
                    "ORDER BY scheduled_at, id", (int(target), *selected, *date_params),
                ).fetchall()
            else:
                exact = connection.execute(
                    f"SELECT * FROM reminders WHERE status IN ({status_marks}) AND title = ? COLLATE NOCASE{date_clause} "
                    "ORDER BY scheduled_at, id", (*selected, target, *date_params),
                ).fetchall()
                rows = exact or connection.execute(
                    f"SELECT * FROM reminders WHERE status IN ({status_marks}) AND instr(lower(title), lower(?)) > 0{date_clause} "
                    "ORDER BY scheduled_at, id LIMIT 20", (*selected, target, *date_params),
                ).fetchall()
        return [_from_row(row) for row in rows]

    def list_status(self, status: str, limit: int = 100, start: datetime | None = None,
                    end: datetime | None = None) -> list[Reminder]:
        if status not in STATUSES:
            raise ValueError("Invalid reminder status")
        clauses, params = ["status = ?"], [status]
        if start is not None:
            clauses.append("scheduled_at >= ?")
            params.append(_timestamp(start))
        if end is not None:
            clauses.append("scheduled_at < ?")
            params.append(_timestamp(end))
        params.append(max(1, min(int(limit), 500)))
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM reminders WHERE {' AND '.join(clauses)} ORDER BY scheduled_at, id LIMIT ?",
                params,
            ).fetchall()
        return [_from_row(row) for row in rows]

    def matching_duplicate(self, title: str, scheduled_at: datetime) -> Reminder | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE status='pending' AND title = ? COLLATE NOCASE AND scheduled_at = ? "
                "ORDER BY id LIMIT 1", (title.strip(), _timestamp(scheduled_at)),
            ).fetchone()
        return _from_row(row)

    def set_status(self, reminder_id: int, status: str, updated_at: datetime | None = None) -> Reminder | None:
        return self.transition_status(reminder_id, status, updated_at)[0]

    def transition_status(self, reminder_id: int, status: str,
                          updated_at: datetime | None = None) -> tuple[Reminder | None, int]:
        """Transition only a pending row, returning the committed row and changed count."""
        if status not in STATUSES:
            raise ValueError("Invalid reminder status")
        timestamp = _timestamp(updated_at or datetime.now().astimezone())
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE reminders SET status = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
                (status, timestamp, reminder_id),
            )
            row = _from_row(connection.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone())
            changed = cursor.rowcount
        return row, changed

    def cancel_pending_between(self, start: datetime, end: datetime,
                               updated_at: datetime | None = None) -> int:
        """Atomically cancel all pending reminders in [start, end); return rows changed."""
        if _timestamp(end) <= _timestamp(start):
            raise ValueError("Cancellation range must be positive")
        timestamp = _timestamp(updated_at or datetime.now().astimezone())
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE reminders SET status = 'cancelled', updated_at = ? "
                "WHERE status = 'pending' AND scheduled_at >= ? AND scheduled_at < ?",
                (timestamp, _timestamp(start), _timestamp(end)),
            )
            changed = cursor.rowcount
        return changed

    def reschedule(self, reminder_id: int, scheduled_at: datetime,
                   updated_at: datetime | None = None) -> Reminder | None:
        scheduled, updated = _timestamp(scheduled_at), _timestamp(updated_at or datetime.now().astimezone())
        with self._connection() as connection:
            connection.execute(
                "UPDATE reminders SET scheduled_at = ?, last_notified_at = NULL, updated_at = ? "
                "WHERE id = ? AND status = 'pending'", (scheduled, updated, reminder_id),
            )
            return _from_row(connection.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone())

    def due_unnotified(self, now: datetime | None = None) -> list[Reminder]:
        timestamp = _timestamp(now or datetime.now().astimezone())
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE status='pending' AND scheduled_at <= ? "
                "AND last_notified_at IS NULL ORDER BY scheduled_at, id", (timestamp,),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def claim_due_notifications(self, reminder_ids: list[int], now: datetime | None = None) -> list[Reminder]:
        """Atomically mark due items before announcement to prevent duplicate claims."""
        if not reminder_ids:
            return []
        timestamp = _timestamp(now or datetime.now().astimezone())
        ids = sorted(set(int(item) for item in reminder_ids))
        placeholders = ",".join("?" for _ in ids)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT * FROM reminders WHERE id IN ({placeholders}) AND status='pending' "
                "AND scheduled_at <= ? AND last_notified_at IS NULL ORDER BY scheduled_at, id",
                (*ids, timestamp),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE reminders SET last_notified_at = ?, updated_at = ? "
                    "WHERE id = ? AND last_notified_at IS NULL", (timestamp, timestamp, row["id"]),
                )
            connection.commit()
        return [_from_row(row) for row in rows]
