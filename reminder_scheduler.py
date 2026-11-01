"""Non-blocking due-reminder polling; callers serialize announcements with speech."""
from queue import Empty, Queue
import threading
import time


class ReminderScheduler:
    def __init__(self, store, interval_seconds: float = 5.0):
        self.store = store
        self.interval_seconds = max(1.0, interval_seconds)
        self._stop = threading.Event()
        self._queue_lock = threading.Lock()
        self._queued: set[int] = set()
        self._queue: Queue[int] = Queue()
        self._thread = threading.Thread(target=self._run, name="leo-reminder-check", daemon=True)

    def start(self):
        self._thread.start()

    def _poll(self):
        for reminder in self.store.due_unnotified():
            with self._queue_lock:
                if reminder.id not in self._queued:
                    self._queued.add(reminder.id)
                    self._queue.put(reminder.id)

    def _run(self):
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as exc:  # scheduler must never take down voice interaction
                print(f"WARNING: reminder check failed ({type(exc).__name__})", flush=True)
            self._stop.wait(self.interval_seconds)

    def claim_due(self):
        """Claim queued due reminders at a safe audio boundary without stopping polling."""
        try:
            self._poll()
            ids = []
            with self._queue_lock:
                while True:
                    try:
                        ids.append(self._queue.get_nowait())
                    except Empty:
                        break
                self._queued.difference_update(ids)
            return self.store.claim_due_notifications(ids) if ids else []
        except Exception as exc:
            print(f"WARNING: could not claim due reminders ({type(exc).__name__})", flush=True)
            return []

    def stop_and_claim_due(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self.interval_seconds + 1)
        return self.claim_due()

